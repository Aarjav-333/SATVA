"""Generate the printable SATVA reference card.

    cd backend && python scripts/generate_reference_card.py

Produces `docs/reference_card/satva_reference_card_v1.pdf` (and a PNG preview)
at exact physical size, 85.6 x 54.0 mm, from the same specification the reader
uses. The geometry is imported from `app.services.colorimetry.reference_card`
rather than redrawn here, so the printed card and the software's idea of the
card cannot drift apart — a 2 mm disagreement would silently sample the wrong
pixels and produce a confident, wrong number.

Printing notes
--------------
* Print at 100% scale ("actual size"), not "fit to page". A scaled card still
  detects, but the strip well no longer lines up with a physical strip.
* Matte paper. Glossy stock produces specular highlights that trip the
  glare check.
* Colour management off, or set to "printer manages colour" with no profile.
  The patch values are sRGB; letting an application re-map them defeats the
  point of a reference.
* A cheap office laser is fine. The correction fit does not need the print to
  be accurate in absolute terms -- it needs it to be *consistent*, and it needs
  the software to know what was printed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reportlab.lib.colors import Color, black, white  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.pdfgen import canvas as pdfcanvas  # noqa: E402

from app.services.colorimetry.reference_card import (  # noqa: E402
    CARD_HEIGHT_MM,
    CARD_WIDTH_MM,
    SATVA_CARD_V1,
    Patch,
    ReferenceCardSpec,
)

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "docs" / "reference_card"


def _colour(patch: Patch) -> Color:
    r, g, b = patch.srgb
    return Color(r / 255.0, g / 255.0, b / 255.0)


def draw_card(c: pdfcanvas.Canvas, spec: ReferenceCardSpec, x0: float, y0: float) -> None:
    """Draw one card with its lower-left corner at (x0, y0) in points."""
    width = CARD_WIDTH_MM * mm
    height = CARD_HEIGHT_MM * mm

    # Card body.
    c.setFillColor(white)
    c.setStrokeColor(black)
    c.setLineWidth(0.6)
    c.rect(x0, y0, width, height, stroke=1, fill=1)

    # Dark border frame. This is what the detector finds: a high-contrast
    # quadrilateral that survives poor lighting.
    c.setFillColor(Color(0.08, 0.08, 0.08))
    frame = 2.6 * mm
    c.rect(x0, y0, width, frame, stroke=0, fill=1)
    c.rect(x0, y0 + height - frame, width, frame, stroke=0, fill=1)
    c.rect(x0, y0, frame, height, stroke=0, fill=1)
    c.rect(x0 + width - frame, y0, frame, height, stroke=0, fill=1)

    def place(patch: Patch) -> None:
        # Normalised coordinates run top-down; PDF runs bottom-up.
        px = x0 + patch.rect[0] * width
        py = y0 + height - (patch.rect[1] + patch.rect[3]) * height
        pw = patch.rect[2] * width
        ph = patch.rect[3] * height
        c.setFillColor(_colour(patch))
        c.setStrokeColor(Color(0.75, 0.75, 0.75))
        c.setLineWidth(0.25)
        c.rect(px, py, pw, ph, stroke=1, fill=1)

    for patch in spec.patches:
        place(patch)
    place(spec.orientation_key)

    # Strip well: an outlined aperture the user lays the reacted strip inside.
    wx, wy, ww, wh = spec.strip_well
    sx = x0 + wx * width
    sy = y0 + height - (wy + wh) * height
    sw = ww * width
    sh = wh * height
    c.setFillColor(white)
    c.setStrokeColor(Color(0.25, 0.25, 0.25))
    c.setLineWidth(0.9)
    c.setDash(2, 2)
    c.rect(sx, sy, sw, sh, stroke=1, fill=1)
    c.setDash()

    c.setFillColor(Color(0.45, 0.45, 0.45))
    c.setFont("Helvetica", 4.6)
    c.drawCentredString(sx + sw / 2, sy + sh / 2 - 1.6, "PLACE TEST STRIP HERE")

    # Identity and version. The software checks nothing here, but a person
    # holding a card needs to know which card they have.
    c.setFillColor(Color(0.35, 0.35, 0.35))
    c.setFont("Helvetica-Bold", 5.4)
    c.drawString(x0 + 3.4 * mm, y0 + 3.4 * mm, "SATVA")
    c.setFont("Helvetica", 4.2)
    c.drawString(x0 + 12 * mm, y0 + 3.4 * mm, f"reference card {spec.version}")
    c.drawRightString(
        x0 + width - 3.4 * mm,
        y0 + 3.4 * mm,
        "screening aid — not a statutory test",
    )


def draw_instructions(c: pdfcanvas.Canvas, page_width: float, top: float) -> None:
    lines = [
        ("Helvetica-Bold", 12, "SATVA reference card v1"),
        ("Helvetica", 8.5,
         "Print at 100% scale (actual size) on matte paper. Do not 'fit to page'."),
        ("Helvetica", 8.5,
         "Turn colour management off, or set 'printer manages colour' with no profile."),
        ("Helvetica", 8.5, ""),
        ("Helvetica-Bold", 9, "Taking a reading"),
        ("Helvetica", 8.5,
         "1.  Lay the card flat and place the reacted strip inside the dashed window."),
        ("Helvetica", 8.5, "2.  Photograph the whole card, square on, from about 20 cm."),
        ("Helvetica", 8.5,
         "3.  Use even light. Avoid direct sun, deep shade, and shadows across the card."),
        ("Helvetica", 8.5, ""),
        ("Helvetica", 8.5,
         "If the photo is not good enough to measure accurately, SATVA will say so"),
        ("Helvetica", 8.5, "and ask you to retake it. It will not report an unreliable number."),
    ]
    y = top
    for font, size, text in lines:
        c.setFont(font, size)
        c.setFillColor(Color(0.1, 0.15, 0.2) if font.endswith("Bold") else Color(0.3, 0.33, 0.36))
        c.drawString(20 * mm, y, text)
        y -= (size + 4.2)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = OUTPUT_DIR / "satva_reference_card_v1.pdf"

    from reportlab.lib.pagesizes import A4

    page_width, page_height = A4
    c = pdfcanvas.Canvas(str(pdf_path), pagesize=A4)
    c.setTitle("SATVA reference card v1")

    draw_instructions(c, page_width, page_height - 25 * mm)

    # Two cards per sheet: one to use, one to keep clean as a spare. Cards get
    # stained in a market, and a stained card is a refused reading.
    draw_card(c, SATVA_CARD_V1, 20 * mm, page_height - 130 * mm)
    draw_card(c, SATVA_CARD_V1, 20 * mm, page_height - 195 * mm)

    c.setFont("Helvetica", 7)
    c.setFillColor(Color(0.55, 0.58, 0.6))
    c.drawString(
        20 * mm,
        page_height - 205 * mm,
        "Two cards per sheet — keep the second clean as a spare. "
        "A stained card is a refused reading.",
    )

    c.showPage()
    c.save()

    print(f"wrote {pdf_path}")
    print(f"  card size   : {CARD_WIDTH_MM} x {CARD_HEIGHT_MM} mm")
    print(f"  patches     : {len(SATVA_CARD_V1.patches)} "
          f"({len(SATVA_CARD_V1.neutral_patches)} neutral, "
          f"{len(SATVA_CARD_V1.chromatic_patches)} chromatic)")
    print(f"  strip well  : {SATVA_CARD_V1.strip_well}")

    # PNG preview, so the card can be shown on screen during a demo without a
    # PDF viewer.
    try:
        import cv2

        from tests.fixtures.card_render import render_card

        preview = render_card(None, SATVA_CARD_V1, strip_present=False)
        png_path = OUTPUT_DIR / "satva_reference_card_v1.png"
        cv2.imwrite(str(png_path), preview)
        print(f"wrote {png_path}")
    except Exception as exc:  # noqa: BLE001
        print(f"  (PNG preview skipped: {exc})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
