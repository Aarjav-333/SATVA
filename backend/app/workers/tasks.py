"""Background tasks.

Each task is a thin wrapper: it opens a session, calls a service, logs the
outcome. The logic lives in the services so it can be tested without a broker.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import session_scope
from app.workers.celery_app import celery_app

log = get_logger("satva.workers")


@celery_app.task(name="satva.watch.recompute_clusters", bind=True, max_retries=3)
def recompute_clusters(self) -> dict:
    """Re-run DBSCAN over the current window and replace stored clusters."""
    from app.services.watch.service import WatchService

    try:
        with session_scope() as db:
            run = WatchService(db).run_clustering()
            result = {
                "run_id": str(run.id),
                "input_readings": run.input_reading_count,
                "clusters": run.cluster_count,
                "publishable": run.publishable_count,
                "duration_ms": run.duration_ms,
            }
        log.info("task_recompute_clusters_done", **result)
        return result
    except Exception as exc:  # noqa: BLE001
        log.error("task_recompute_clusters_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=60) from exc


@celery_app.task(name="satva.trace.publish_merkle_root", bind=True, max_retries=3)
def publish_merkle_root(self, for_date: str | None = None) -> dict:
    """Publish the daily Merkle root over the previous day's custody events.

    Anchoring yesterday rather than today means the covered period is closed:
    a root over a still-open day would need republishing as events arrive.
    """
    from app.models.trace import CustodyEvent
    from app.models.watch import MerkleRoot
    from app.services.trace.hashchain import MERKLE_ALGORITHM, merkle_root

    target = (
        datetime.fromisoformat(for_date).replace(tzinfo=UTC)
        if for_date
        else datetime.now(UTC) - timedelta(days=1)
    )
    start = target.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)

    try:
        with session_scope() as db:
            events = list(
                db.scalars(
                    select(CustodyEvent)
                    .where(CustodyEvent.created_at >= start, CustodyEvent.created_at < end)
                    # Deterministic leaf order: two independent recomputations of
                    # the same day must produce the same root.
                    .order_by(CustodyEvent.created_at, CustodyEvent.event_hash)
                )
            )
            leaves = [e.event_hash for e in events]
            root_hash = merkle_root(leaves)

            existing = db.scalar(select(MerkleRoot).where(MerkleRoot.period_date == start))
            if existing is not None:
                if existing.root_hash != root_hash:
                    # A published root that no longer matches means covered
                    # history changed. That is the exact condition anchoring
                    # exists to surface, so it is logged loudly and the
                    # published root is left untouched.
                    log.error(
                        "merkle_root_divergence_detected",
                        period=start.isoformat(),
                        published_root=existing.root_hash,
                        recomputed_root=root_hash,
                        note="covered custody records appear to have changed after publication",
                    )
                return {
                    "period_date": start.isoformat(),
                    "root_hash": existing.root_hash,
                    "leaf_count": existing.leaf_count,
                    "already_published": True,
                    "matches_recomputation": existing.root_hash == root_hash,
                }

            record = MerkleRoot(
                period_date=start,
                root_hash=root_hash,
                leaf_count=len(leaves),
                algorithm=MERKLE_ALGORITHM,
                covered_from=start,
                covered_to=end,
                published_at=datetime.now(UTC),
            )
            db.add(record)
            db.flush()
            for event in events:
                event.merkle_root_id = record.id

            result = {
                "period_date": start.isoformat(),
                "root_hash": root_hash,
                "leaf_count": len(leaves),
                "already_published": False,
            }
        log.info("task_publish_merkle_root_done", **result)
        return result
    except Exception as exc:  # noqa: BLE001
        log.error("task_publish_merkle_root_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=120) from exc


@celery_app.task(name="satva.retention.sweep_expired_images")
def sweep_expired_images() -> dict:
    """Delete evidence images past their retention deadline (spec 15.3).

    Images belonging to a live complaint carry an extended deadline set when the
    complaint was created, so this sweep will not remove evidence still in use.
    """
    from app.models.scan import ScanImage
    from app.services.storage import object_store

    now = datetime.now(UTC)
    deleted = 0
    failed = 0

    with session_scope() as db:
        expired = db.scalars(
            select(ScanImage).where(
                ScanImage.retain_until.is_not(None),
                ScanImage.retain_until < now,
                ScanImage.deleted_at.is_(None),
            )
        )
        for image in expired:
            try:
                object_store.delete(image.object_key)
                image.deleted_at = now
                deleted += 1
            except Exception as exc:  # noqa: BLE001
                # Leave the row untouched so the next sweep retries it. Marking
                # it deleted when the object still exists would strand data.
                log.warning(
                    "retention_delete_failed", object_key=image.object_key, error=str(exc)[:120]
                )
                failed += 1

    result = {"deleted": deleted, "failed": failed}
    log.info("task_sweep_expired_images_done", **result)
    return result


@celery_app.task(name="satva.shelf.refresh_predictions")
def refresh_predictions() -> dict:
    """Recompute shelf-life predictions for all active stock.

    Predictions age: a crate that was 'sell normally' this morning may be
    'markdown' by evening simply because time passed and the crate sat warm.
    """
    from app.models.retail import InventoryItem, ShelfPrediction
    from app.models.trace import TemperatureTagReading
    from app.services.shelf.predictor import predict

    updated = 0
    with session_scope() as db:
        items = list(
            db.scalars(select(InventoryItem).where(InventoryItem.withdrawn_at.is_(None)))
        )
        for item in items:
            previous = db.scalar(
                select(ShelfPrediction)
                .where(ShelfPrediction.inventory_item_id == item.id)
                .order_by(ShelfPrediction.created_at.desc())
                .limit(1)
            )
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
                # Carry the last measured ripeness forward; the vision head only
                # runs when someone photographs the crate.
                ripeness_index=previous.ripeness_index if previous else None,
                received_at=item.received_at,
                temperature_readings=readings,
            )
            db.add(
                ShelfPrediction(
                    inventory_item_id=item.id,
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
            )
            updated += 1

    result = {"items_updated": updated}
    log.info("task_refresh_predictions_done", **result)
    return result
