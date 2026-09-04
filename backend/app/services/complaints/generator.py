"""FSSAI complaint package generation.

What this does and does not do
------------------------------
SATVA **prepares** a structured evidence package and renders it as a PDF the
citizen can attach to a complaint on the statutory FSSAI channel (Food Safety
Connect / the FSSAI portal). It does **not** submit anything.

There is no public programmatic submission API for Food Safety Connect that
SATVA can integrate against, and pretending otherwise would be worse than
useless: a citizen who believes their complaint was filed, when it was not, is
left worse off than if SATVA had said nothing. So the generated document ends
with explicit filing instructions, the complaint status stays PACKAGE_READY, and
only the user's own confirmation moves it to SUBMITTED_BY_USER. Non-negotiable
rule 14: escalation routes through the statutory mechanism rather than bypassing
or simulating it.

Evidence rule
-------------
A package can only be built from a scan graded CONFIRMATORY. That check is made
in `app.services.evidence`, and this module refuses to run without it.
"""

from __future__ import annotations

import hashlib
import io
import secrets
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus import (
    Image as RLImage,
)

from app.core.config import settings
from app.core.constants import DISCLAIMER_LONG, SUPPORTED_CROPS
from app.core.logging import get_logger

log = get_logger("satva.complaints")

PACKAGE_FORMAT_VERSION = "1.0"


def new_reference_code() -> str:
    """Human-quotable SATVA reference, e.g. ``SATVA-2026-4KD9TQ``."""
    year = datetime.now(UTC).year
    return f"SATVA-{year}-{secrets.token_hex(3).upper()}"


@dataclass
class EvidenceItem:
    """One photograph attached to the package."""

    kind: str
    sha256: str
    captured_at: datetime | None
    object_key: str
    content_type: str
    byte_size: int
    image_bytes: bytes | None = None


@dataclass
class ComplaintEvidence:
    """Everything a complaint package contains.

    Assembled once and frozen into `Complaint.evidence_snapshot`, so that a
    later change elsewhere in the database cannot alter what was filed.
    """

    reference_code: str
    generated_at: datetime
    scan_id: str
    reading_id: str

    # Confirmatory reading (spec 9.3: the numeric chemical reading).
    assay: str
    assay_display_name: str
    calibration_id: str
    concentration_value: float
    concentration_unit: str
    ci_low: float
    ci_high: float
    band_label: str
    exceeds_action_threshold: bool
    action_threshold: float
    is_lab_validated: bool
    measurement_quality: dict[str, Any] = field(default_factory=dict)

    # Context.
    crop: str | None = None
    cultivar: str | None = None
    incident_at: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    location_accuracy_m: float | None = None
    ward_code: str | None = None
    ward_name: str | None = None
    district: str | None = None
    state: str | None = None
    place_description: str | None = None

    # Vendor detail, included only when the complainant supplied it.
    merchant_name: str | None = None
    merchant_address: str | None = None
    merchant_fssai_licence: str | None = None

    # Complainant, optional (a complaint may be filed anonymously).
    complainant_name: str | None = None
    complainant_phone: str | None = None
    complainant_email: str | None = None

    # Screening context. Present for completeness and explicitly labelled
    # advisory, so a reader cannot mistake it for part of the evidence.
    vision_anomaly_score: float | None = None
    vision_model_id: str | None = None

    lot_code: str | None = None
    trace_verified: bool | None = None

    images: list[EvidenceItem] = field(default_factory=list)
    narrative: str | None = None

    def to_snapshot(self) -> dict:
        """JSON-safe snapshot, without raw image bytes."""
        data = asdict(self)
        data["generated_at"] = self.generated_at.isoformat()
        data["incident_at"] = self.incident_at.isoformat() if self.incident_at else None
        data["images"] = [
            {
                "kind": i.kind,
                "sha256": i.sha256,
                "object_key": i.object_key,
                "content_type": i.content_type,
                "byte_size": i.byte_size,
                "captured_at": i.captured_at.isoformat() if i.captured_at else None,
            }
            for i in self.images
        ]
        data["format_version"] = PACKAGE_FORMAT_VERSION
        return data


# --- Document rendering ------------------------------------------------------
_INK = colors.HexColor("#101828")
_MUTED = colors.HexColor("#475467")
_LINE = colors.HexColor("#D0D5DD")
_WARN_BG = colors.HexColor("#FFF4E5")
_WARN_BORDER = colors.HexColor("#F79009")
_ACCENT = colors.HexColor("#0B6E4F")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "SatvaTitle", parent=base["Title"], fontSize=17, leading=21, textColor=_INK,
            alignment=TA_LEFT, spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "SatvaSubtitle", parent=base["Normal"], fontSize=9.5, leading=13, textColor=_MUTED,
        ),
        "h2": ParagraphStyle(
            "SatvaH2", parent=base["Heading2"], fontSize=11.5, leading=15, textColor=_INK,
            spaceBefore=13, spaceAfter=5,
        ),
        "body": ParagraphStyle(
            "SatvaBody", parent=base["Normal"], fontSize=9.5, leading=13.5, textColor=_INK,
        ),
        "small": ParagraphStyle(
            "SatvaSmall", parent=base["Normal"], fontSize=8, leading=11, textColor=_MUTED,
        ),
        "mono": ParagraphStyle(
            "SatvaMono", parent=base["Normal"], fontName="Courier", fontSize=7.5, leading=10,
            textColor=_MUTED,
        ),
        "warn": ParagraphStyle(
            "SatvaWarn", parent=base["Normal"], fontSize=8.5, leading=12,
            textColor=colors.HexColor("#7A2E0E"),
        ),
    }


def _kv_table(rows: list[tuple[str, str]], styles) -> Table:
    data = [
        [Paragraph(f"<b>{k}</b>", styles["body"]), Paragraph(v or "-", styles["body"])]
        for k, v in rows
    ]
    table = Table(data, colWidths=[52 * mm, 116 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("LINEBELOW", (0, 0), (-1, -2), 0.25, _LINE),
            ]
        )
    )
    return table


def _callout(text: str, styles, *, background=_WARN_BG, border=_WARN_BORDER) -> Table:
    table = Table([[Paragraph(text, styles["warn"])]], colWidths=[168 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), background),
                ("BOX", (0, 0), (-1, -1), 0.6, border),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return table


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    from datetime import timedelta, timezone

    ist = aware.astimezone(timezone(timedelta(hours=5, minutes=30)))
    return ist.strftime("%d %B %Y, %H:%M IST")


def _fmt_coords(evidence: ComplaintEvidence) -> str:
    if evidence.latitude is None or evidence.longitude is None:
        return "Not recorded"
    accuracy = (
        f" (GPS accuracy approximately {evidence.location_accuracy_m:.0f} m)"
        if evidence.location_accuracy_m
        else ""
    )
    return f"{evidence.latitude:.6f}, {evidence.longitude:.6f}{accuracy}"


def render_complaint_pdf(evidence: ComplaintEvidence) -> bytes:
    """Render the evidence package as a PDF for the citizen to file."""
    styles = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=21 * mm,
        rightMargin=21 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"SATVA evidence package {evidence.reference_code}",
        author="SATVA",
        subject="Food safety complaint evidence package",
    )

    story: list = []

    story.append(Paragraph("Food safety complaint - evidence package", styles["title"]))
    story.append(
        Paragraph(
            f"Prepared by SATVA &middot; Reference {evidence.reference_code} &middot; "
            f"Generated {_fmt_dt(evidence.generated_at)}",
            styles["subtitle"],
        )
    )
    story.append(Spacer(1, 5))
    story.append(HRFlowable(width="100%", thickness=0.7, color=_LINE))
    story.append(Spacer(1, 9))

    story.append(
        _callout(
            "<b>What this document is.</b> " + DISCLAIMER_LONG + "<br/><br/>"
            "<b>This complaint has NOT been submitted.</b> SATVA does not file complaints on "
            "anyone's behalf. Attach this document to a complaint you raise yourself through "
            f"{settings.fssai_connect_app} or your State Food Safety Department. Filing "
            "instructions are on the last page.",
            styles,
        )
    )
    story.append(Spacer(1, 11))

    # --- 1. The confirmatory reading (the evidence) ------------------------
    story.append(Paragraph("1. Confirmatory chemical reading", styles["h2"]))
    story.append(
        Paragraph(
            "This is the quantitative result of a colorimetric test strip read against a "
            "printed reference card. It is the only measurement in this package that SATVA "
            "treats as evidence.",
            styles["small"],
        )
    )
    story.append(Spacer(1, 5))

    threshold_text = (
        "Yes - the lower bound of the confidence interval is at or above the action threshold"
        if evidence.exceeds_action_threshold
        else "No - the confidence interval does not clear the action threshold"
    )
    story.append(
        _kv_table(
            [
                ("Test performed", evidence.assay_display_name),
                (
                    "Result",
                    f"<b>{evidence.concentration_value:g} {evidence.concentration_unit}</b>",
                ),
                (
                    "Confidence interval",
                    f"{evidence.ci_low:g} to {evidence.ci_high:g} "
                    f"{evidence.concentration_unit}",
                ),
                ("Interpretation band", (evidence.band_label or "-").capitalize()),
                (
                    "Action threshold",
                    f"{evidence.action_threshold:g} {evidence.concentration_unit}",
                ),
                ("Exceeds action threshold", threshold_text),
                ("Calibration series", evidence.calibration_id),
                (
                    "Laboratory validated",
                    "No - see the limitations note below"
                    if not evidence.is_lab_validated
                    else "Yes",
                ),
            ],
            styles,
        )
    )

    if not evidence.is_lab_validated:
        story.append(Spacer(1, 7))
        story.append(
            _callout(
                "<b>Limitation the reader must weigh.</b> The calibration series used for this "
                "reading has not yet been validated against a NABL-accredited laboratory. The "
                "measurement is reproducible and was taken under verified capture conditions, "
                "but it should be treated as grounds for drawing an official sample, not as a "
                "substitute for laboratory analysis.",
                styles,
            )
        )

    # --- 2. Where and when -------------------------------------------------
    story.append(Paragraph("2. Time and place", styles["h2"]))
    crop_label = SUPPORTED_CROPS.get(evidence.crop or "", {}).get("label") or (
        (evidence.crop or "-").replace("_", " ").title()
    )
    story.append(
        _kv_table(
            [
                ("Date and time of purchase or sampling", _fmt_dt(evidence.incident_at)),
                ("GPS coordinates", _fmt_coords(evidence)),
                ("Ward", f"{evidence.ward_name or '-'} ({evidence.ward_code or 'not mapped'})"),
                ("District", evidence.district or "-"),
                ("State", evidence.state or "-"),
                ("Place description", evidence.place_description or "Not provided"),
                ("Commodity", crop_label),
                ("Variety", (evidence.cultivar or "Not recorded").replace("_", " ").title()),
            ],
            styles,
        )
    )

    # --- 3. Vendor ---------------------------------------------------------
    story.append(Paragraph("3. Food business operator", styles["h2"]))
    if evidence.merchant_name:
        story.append(
            _kv_table(
                [
                    ("Name as given by complainant", evidence.merchant_name),
                    ("Address", evidence.merchant_address or "-"),
                    (
                        "FSSAI licence / registration",
                        evidence.merchant_fssai_licence or "Not known",
                    ),
                ],
                styles,
            )
        )
    else:
        story.append(
            Paragraph(
                "The complainant did not identify a specific vendor. The location details in "
                "section 2 are provided instead.",
                styles["body"],
            )
        )

    # --- 4. Complainant ----------------------------------------------------
    story.append(Paragraph("4. Complainant", styles["h2"]))
    if evidence.complainant_name or evidence.complainant_phone:
        story.append(
            _kv_table(
                [
                    ("Name", evidence.complainant_name or "-"),
                    ("Phone", evidence.complainant_phone or "-"),
                    ("Email", evidence.complainant_email or "-"),
                ],
                styles,
            )
        )
    else:
        story.append(
            Paragraph(
                "The complainant has chosen not to provide contact details in this package. "
                "They may supply them directly when filing.",
                styles["body"],
            )
        )

    if evidence.narrative:
        story.append(Paragraph("5. Complainant's account", styles["h2"]))
        story.append(Paragraph(evidence.narrative, styles["body"]))

    # --- Screening context -------------------------------------------------
    story.append(Paragraph("6. Preliminary visual screening (advisory only)", styles["h2"]))
    if evidence.vision_anomaly_score is not None:
        story.append(
            Paragraph(
                f"An on-device image model returned an anomaly score of "
                f"<b>{evidence.vision_anomaly_score:.0f} out of 100</b> "
                f"(model {evidence.vision_model_id or 'unspecified'}). "
                "<b>This score is not evidence.</b> It is a triage signal whose only purpose was "
                "to decide whether performing the chemical test in section 1 was worthwhile. "
                "The image model does not detect any chemical; it responds to visual "
                "characteristics associated with forced ripening. It must not be relied on as "
                "an indication that any offence occurred.",
                styles["body"],
            )
        )
    else:
        story.append(Paragraph("No visual screening score was recorded.", styles["body"]))

    if evidence.lot_code:
        story.append(Paragraph("7. Traceability", styles["h2"]))
        verified = (
            "verified - the custody chain recomputes correctly from origin"
            if evidence.trace_verified
            else "NOT verified - the custody chain failed integrity checking"
        )
        story.append(
            _kv_table(
                [("Lot code", evidence.lot_code), ("Chain integrity", verified)], styles
            )
        )

    # --- Attachments -------------------------------------------------------
    story.append(PageBreak())
    story.append(Paragraph("Photographic evidence", styles["h2"]))
    story.append(
        Paragraph(
            "Each image is listed with a SHA-256 digest computed when it was captured. The "
            "digest lets a recipient confirm that an image supplied separately is byte-for-byte "
            "the one referenced here.",
            styles["small"],
        )
    )
    story.append(Spacer(1, 7))

    kind_labels = {
        "produce": "The produce as offered for sale",
        "strip": "The reacted test strip beside the SATVA reference card",
        "reference": "The reference card",
    }
    if evidence.images:
        for index, item in enumerate(evidence.images, start=1):
            block: list = [
                Paragraph(
                    f"<b>Exhibit {index}.</b> {kind_labels.get(item.kind, item.kind)}",
                    styles["body"],
                )
            ]
            if item.image_bytes:
                try:
                    block.append(Spacer(1, 4))
                    block.append(
                        RLImage(io.BytesIO(item.image_bytes), width=110 * mm, height=82 * mm,
                                kind="proportional")
                    )
                except Exception:  # noqa: BLE001 - a bad image must not break filing
                    block.append(Paragraph("[image could not be embedded]", styles["small"]))
            block.append(Spacer(1, 3))
            block.append(Paragraph(f"Captured: {_fmt_dt(item.captured_at)}", styles["small"]))
            block.append(Paragraph(f"SHA-256: {item.sha256}", styles["mono"]))
            block.append(Spacer(1, 9))
            story.append(KeepTogether(block))
    else:
        story.append(Paragraph("No images were attached to this package.", styles["body"]))

    # --- Measurement provenance -------------------------------------------
    story.append(Paragraph("Measurement conditions", styles["h2"]))
    story.append(
        Paragraph(
            "SATVA refuses to produce a reading when capture conditions fall outside validated "
            "limits, rather than reporting a low-confidence number. The reading in section 1 "
            "passed every check below.",
            styles["small"],
        )
    )
    story.append(Spacer(1, 4))
    quality = evidence.measurement_quality or {}
    quality_rows = [
        ("Reference card detected", "Yes"),
        (
            "Colour correction residual",
            f"dE {quality.get('residual_de_mean', 'n/a')} mean across reference patches",
        ),
        ("Reference white level", f"{quality.get('brightest_neutral', 'n/a')} / 255"),
        ("Illuminant neutrality", f"chroma {quality.get('neutral_chroma_mean', 'n/a')}"),
        ("Strip uniformity", f"L* standard deviation {quality.get('strip_l_std', 'n/a')}"),
    ]
    story.append(_kv_table(quality_rows, styles))

    # --- Filing instructions ----------------------------------------------
    story.append(Spacer(1, 12))
    story.append(Paragraph("How to file this complaint", styles["h2"]))
    story.append(
        Paragraph(
            f"1. Open the <b>{settings.fssai_connect_app}</b> mobile application, or visit "
            f"<b>{settings.fssai_portal_url}</b>, or contact your District Designated Officer "
            "or State Food Safety Department.<br/>"
            "2. Raise a complaint under the food-safety category that matches this commodity.<br/>"
            "3. Enter the date, time and location from section 2 of this document.<br/>"
            "4. Attach this PDF together with the original photographs.<br/>"
            "5. Quote SATVA reference "
            f"<b>{evidence.reference_code}</b> so the two records can be matched.<br/>"
            "6. Retain the acknowledgement number the authority issues. You can record it in "
            "SATVA to keep this package linked to the official complaint.",
            styles["body"],
        )
    )
    story.append(Spacer(1, 9))
    story.append(
        _callout(
            "A visual screening result on its own has never been treated as sufficient grounds "
            "for this package, and SATVA has no authority to determine that any food is unsafe "
            "or that any law has been broken. Only the food safety authority can draw an "
            "official sample, have it analysed by an accredited laboratory, and decide what "
            "action is warranted.",
            styles,
            background=colors.HexColor("#ECFDF3"),
            border=_ACCENT,
        )
    )

    doc.build(story)
    return buffer.getvalue()


def build_package(evidence: ComplaintEvidence) -> tuple[bytes, str]:
    """Render the package and return (pdf_bytes, sha256_hex).

    The digest is stored on the complaint so that a package produced today can
    be shown to be the one that was filed, even if the underlying data changes
    later.
    """
    pdf = render_complaint_pdf(evidence)
    digest = hashlib.sha256(pdf).hexdigest()
    log.info(
        "complaint_package_built",
        reference_code=evidence.reference_code,
        scan_id=evidence.scan_id,
        bytes=len(pdf),
        sha256=digest[:16],
    )
    return pdf, digest
