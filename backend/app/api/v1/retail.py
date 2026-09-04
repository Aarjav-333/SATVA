"""/retail -- SATVA Shelf for retailers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Principal, require_roles
from app.core.constants import Role, ShelfAction
from app.core.errors import NotFoundError, PermissionDeniedError
from app.db.session import get_db
from app.models.retail import (
    InventoryItem,
    RetailOutlet,
    ShelfPrediction,
    SpoilageEvent,
)
from app.models.trace import ProduceLot, TemperatureTagReading
from app.schemas.retail import (
    InventoryItemCreate,
    InventoryItemOut,
    OutletOut,
    ShelfPredictionOut,
    ShelfSummaryOut,
    SpoilageAnalyticsOut,
    SpoilageTrendPoint,
    TemperatureReadingIn,
)
from app.services.shelf.predictor import predict

router = APIRouter(prefix="/retail", tags=["retail"])
require_retail = require_roles(Role.RETAILER, Role.OFFICER)


def _outlet_for(
    principal: Principal, db: Session, outlet_id: uuid.UUID | None = None
) -> RetailOutlet:
    """Resolve the outlet the caller may act on.

    A retailer is scoped to outlets they own. Ownership is checked here rather
    than assumed from the URL, so passing another outlet's id does not grant
    access to its inventory.
    """
    if outlet_id is not None:
        outlet = db.get(RetailOutlet, outlet_id)
        if outlet is None:
            raise NotFoundError("No such outlet.")
        if (
            principal.role is Role.RETAILER
            and outlet.owner_user_id is not None
            and outlet.owner_user_id != principal.id
        ):
            raise PermissionDeniedError("That outlet belongs to a different account.")
        return outlet

    outlet = db.scalar(
        select(RetailOutlet).where(RetailOutlet.owner_user_id == principal.id).limit(1)
    )
    if outlet is None:
        # Officers and admins browsing the demo get the first seeded outlet.
        outlet = db.scalar(select(RetailOutlet).limit(1))
    if outlet is None:
        raise NotFoundError("No retail outlet is registered for this account.")
    return outlet


def _latest_prediction(db: Session, item_id: uuid.UUID) -> ShelfPrediction | None:
    return db.scalar(
        select(ShelfPrediction)
        .where(ShelfPrediction.inventory_item_id == item_id)
        .order_by(ShelfPrediction.created_at.desc())
        .limit(1)
    )


def _item_out(db: Session, item: InventoryItem) -> InventoryItemOut:
    prediction = _latest_prediction(db, item.id)
    lot = db.get(ProduceLot, item.lot_id) if item.lot_id else None

    trace_status = "no_trace"
    if lot is not None:
        from app.services.trace.service import TraceService

        trace_status = (
            "verified" if TraceService(db).verify_ancestry(lot.id)["valid"] else "chain_broken"
        )

    return InventoryItemOut(
        id=item.id,
        sku=item.sku,
        crop=item.crop,
        cultivar=item.cultivar,
        quantity_kg=float(item.quantity_kg),
        unit_price_inr=float(item.unit_price_inr) if item.unit_price_inr is not None else None,
        received_at=item.received_at,
        display_location=item.display_location,
        ble_tag_ref=item.ble_tag_ref,
        lot_id=item.lot_id,
        lot_code=lot.lot_code if lot else None,
        trace_status=trace_status,
        days_in_stock=round(
            (datetime.now(UTC) - item.received_at).total_seconds() / 86400.0, 2
        ),
        latest_prediction=(
            ShelfPredictionOut.model_validate(prediction) if prediction else None
        ),
        is_synthetic=item.is_synthetic,
    )


@router.get("/outlets", response_model=list[OutletOut], summary="Outlets you can manage")
def list_outlets(
    principal: Principal = Depends(require_retail), db: Session = Depends(get_db)
) -> list[OutletOut]:
    statement = select(RetailOutlet).order_by(RetailOutlet.name)
    if principal.role is Role.RETAILER:
        statement = statement.where(RetailOutlet.owner_user_id == principal.id)
    return [OutletOut.model_validate(o) for o in db.scalars(statement)]


@router.get("/inventory", response_model=list[InventoryItemOut], summary="Current stock")
def list_inventory(
    outlet_id: uuid.UUID | None = Query(None),
    action: ShelfAction | None = Query(None),
    principal: Principal = Depends(require_retail),
    db: Session = Depends(get_db),
) -> list[InventoryItemOut]:
    outlet = _outlet_for(principal, db, outlet_id)
    items = db.scalars(
        select(InventoryItem)
        .where(InventoryItem.outlet_id == outlet.id, InventoryItem.withdrawn_at.is_(None))
        .order_by(InventoryItem.received_at)
    )
    results = [_item_out(db, item) for item in items]
    if action is not None:
        results = [
            r
            for r in results
            if r.latest_prediction and r.latest_prediction.recommended_action == action
        ]
    return results


@router.post(
    "/inventory",
    response_model=InventoryItemOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add stock",
)
def add_inventory(
    payload: InventoryItemCreate,
    outlet_id: uuid.UUID | None = Query(None),
    principal: Principal = Depends(require_retail),
    db: Session = Depends(get_db),
) -> InventoryItemOut:
    outlet = _outlet_for(principal, db, outlet_id)
    item = InventoryItem(
        outlet_id=outlet.id,
        lot_id=payload.lot_id,
        sku=payload.sku,
        crop=payload.crop.lower(),
        cultivar=payload.cultivar,
        quantity_kg=payload.quantity_kg,
        unit_price_inr=payload.unit_price_inr,
        received_at=payload.received_at or datetime.now(UTC),
        display_location=payload.display_location,
        ble_tag_ref=payload.ble_tag_ref,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _item_out(db, item)


@router.post(
    "/inventory/{item_id}/predict",
    response_model=ShelfPredictionOut,
    summary="Predict remaining shelf life for one item",
    description=(
        "Combines the ripeness index from the vision model's regression head with accumulated "
        "degree-hours from the crate's BLE temperature tag. The response carries a full "
        "`rationale` so a shop manager can see every term behind the recommendation."
    ),
)
def predict_item(
    item_id: uuid.UUID,
    ripeness_index: float | None = Query(None, ge=0, le=1),
    scan_id: uuid.UUID | None = Query(None),
    principal: Principal = Depends(require_retail),
    db: Session = Depends(get_db),
) -> ShelfPredictionOut:
    item = db.get(InventoryItem, item_id)
    if item is None:
        raise NotFoundError("No such inventory item.")
    _outlet_for(principal, db, item.outlet_id)

    # Prefer an explicit value, then the ripeness head's output on a linked scan.
    ripeness = ripeness_index
    if ripeness is None and scan_id is not None:
        from app.models.scan import Scan

        scan = db.get(Scan, scan_id)
        ripeness = scan.ripeness_index if scan else None

    readings = [
        (r.recorded_at, r.temperature_c)
        for r in db.scalars(
            select(TemperatureTagReading)
            .where(
                TemperatureTagReading.tag_ref == (item.ble_tag_ref or "__none__"),
                TemperatureTagReading.recorded_at >= item.received_at,
            )
            .order_by(TemperatureTagReading.recorded_at)
        )
    ]

    result = predict(
        crop=item.crop,
        ripeness_index=ripeness,
        received_at=item.received_at,
        temperature_readings=readings,
    )

    prediction = ShelfPrediction(
        inventory_item_id=item.id,
        scan_id=scan_id,
        ripeness_index=result.ripeness_index,
        freshness_score=result.freshness_score,
        remaining_shelf_life_days=result.remaining_shelf_life_days,
        confidence_low_days=result.confidence_low_days,
        confidence_high_days=result.confidence_high_days,
        mean_temperature_c=result.mean_temperature_c,
        accumulated_degree_hours=result.accumulated_degree_hours,
        temperature_source=result.temperature_source,
        recommended_action=result.recommended_action,
        suggested_markdown_pct=result.suggested_markdown_pct,
        rationale=result.rationale,
        model_id=result.model_id,
        is_synthetic=item.is_synthetic,
    )
    db.add(prediction)
    db.commit()
    db.refresh(prediction)
    return ShelfPredictionOut.model_validate(prediction)


@router.post(
    "/temperature",
    response_model=dict,
    summary="Record a BLE crate-tag temperature reading",
    description=(
        "In this build readings arrive from the dashboard's simulator or the SATVA Node MQTT "
        "bridge. A real BLE tag integration is documented but not implemented; "
        "`is_simulated` records which."
    ),
)
def record_temperature(
    payload: TemperatureReadingIn,
    principal: Principal = Depends(require_retail),
    db: Session = Depends(get_db),
) -> dict:
    reading = TemperatureTagReading(
        lot_id=payload.lot_id,
        tag_ref=payload.tag_ref,
        temperature_c=payload.temperature_c,
        humidity_pct=payload.humidity_pct,
        recorded_at=payload.recorded_at or datetime.now(UTC),
        source=payload.source,
        is_simulated=payload.is_simulated,
    )
    db.add(reading)
    db.commit()
    return {"id": str(reading.id), "recorded_at": reading.recorded_at.isoformat()}


@router.get("/summary", response_model=ShelfSummaryOut, summary="Shelf dashboard summary")
def shelf_summary(
    outlet_id: uuid.UUID | None = Query(None),
    principal: Principal = Depends(require_retail),
    db: Session = Depends(get_db),
) -> ShelfSummaryOut:
    outlet = _outlet_for(principal, db, outlet_id)
    items = list(
        db.scalars(
            select(InventoryItem).where(
                InventoryItem.outlet_id == outlet.id, InventoryItem.withdrawn_at.is_(None)
            )
        )
    )

    counts: dict[str, int] = {a.value: 0 for a in ShelfAction}
    at_risk_value = 0.0
    traced = 0
    for item in items:
        prediction = _latest_prediction(db, item.id)
        if prediction:
            counts[prediction.recommended_action] = counts.get(prediction.recommended_action, 0) + 1
            if prediction.recommended_action in (
                ShelfAction.MARKDOWN,
                ShelfAction.DONATE,
                ShelfAction.WITHDRAW,
            ):
                at_risk_value += float(item.quantity_kg) * float(item.unit_price_inr or 0)
        if item.lot_id:
            traced += 1

    return ShelfSummaryOut(
        outlet=OutletOut.model_validate(outlet),
        total_items=len(items),
        total_kg=round(sum(float(i.quantity_kg) for i in items), 2),
        action_counts=counts,
        at_risk_value_inr=round(at_risk_value, 2),
        traced_item_count=traced,
        generated_at=datetime.now(UTC),
        demo_data_included=any(i.is_synthetic for i in items),
    )


@router.get(
    "/spoilage", response_model=SpoilageAnalyticsOut, summary="Spoilage and waste analytics"
)
def spoilage_analytics(
    outlet_id: uuid.UUID | None = Query(None),
    window_days: int = Query(30, ge=1, le=365),
    principal: Principal = Depends(require_retail),
    db: Session = Depends(get_db),
) -> SpoilageAnalyticsOut:
    outlet = _outlet_for(principal, db, outlet_id)
    since = (datetime.now(UTC) - timedelta(days=window_days)).date()

    rows = db.execute(
        select(
            SpoilageEvent.occurred_on,
            SpoilageEvent.disposition,
            func.sum(SpoilageEvent.quantity_kg),
            func.sum(SpoilageEvent.value_inr),
        )
        .where(SpoilageEvent.outlet_id == outlet.id, SpoilageEvent.occurred_on >= since)
        .group_by(SpoilageEvent.occurred_on, SpoilageEvent.disposition)
        .order_by(SpoilageEvent.occurred_on)
    ).all()

    by_day: dict = {}
    for day, disposition, kg, value in rows:
        entry = by_day.setdefault(
            day, {"discarded_kg": 0.0, "donated_kg": 0.0, "marked_down_kg": 0.0, "value_inr": 0.0}
        )
        key = {
            "discarded": "discarded_kg",
            "donated": "donated_kg",
            "marked_down": "marked_down_kg",
        }.get(disposition, "discarded_kg")
        entry[key] += float(kg or 0)
        entry["value_inr"] += float(value or 0)

    points = [SpoilageTrendPoint(day=day, **values) for day, values in sorted(by_day.items())]
    demo = db.scalar(
        select(func.count())
        .select_from(SpoilageEvent)
        .where(SpoilageEvent.outlet_id == outlet.id, SpoilageEvent.is_synthetic.is_(True))
    )

    return SpoilageAnalyticsOut(
        outlet_id=outlet.id,
        window_days=window_days,
        points=points,
        total_discarded_kg=round(sum(p.discarded_kg for p in points), 2),
        total_donated_kg=round(sum(p.donated_kg for p in points), 2),
        total_value_inr=round(sum(p.value_inr for p in points), 2),
        demo_data_included=bool(demo),
    )
