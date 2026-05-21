"""
Generate a printable PDF version of the tech pack using reportlab.
"""

from datetime import datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
import base64

from reportlab.platypus import (
    Image as RLImage,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from PIL import Image as PILImage


def _styles():
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "TPTitle", parent=base["Heading1"],
            fontSize=20, alignment=1, spaceAfter=12,
        ),
        "h2": ParagraphStyle(
            "TPH2", parent=base["Heading2"],
            fontSize=12, textColor=colors.HexColor("#222222"),
            spaceBefore=14, spaceAfter=6,
        ),
        "normal": ParagraphStyle(
            "TPNormal", parent=base["Normal"],
            fontSize=9, leading=12,
        ),
        "small": ParagraphStyle(
            "TPSmall", parent=base["Normal"],
            fontSize=8, textColor=colors.grey,
        ),
    }
    return styles


def _kv_table(items: list, col_widths=(5 * cm, 7 * cm)) -> Table:
    """Render a list of (label, value) tuples as a 2-column table."""
    rows = [[label, value if value is not None else "—"] for label, value in items]
    table = Table(rows, colWidths=col_widths)
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
        ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#444444")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
    ]))
    return table


def _measurement_table(measurements: dict, base_size: str) -> Table:
    header = ["Measurement Point", f"Size {base_size or 'M'}", "Tolerance"]
    rows = [header]
    for point, vals in measurements.items():
        rows.append([
            point,
            f"{vals.get('value', 0)} cm",
            f"± {vals.get('tolerance', 0)} cm",
        ])
    table = Table(rows, colWidths=(7 * cm, 4 * cm, 4 * cm))
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#333333")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f5f5f5")]),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
    ]))
    return table


def _render_images(images: list, max_per_row: int = 2) -> list:
    """Lay out images in a 2-column table, each cell with the image + caption.

    Returns a list of flowables to append to the PDF story.
    """
    flowables = []
    styles = _styles()
    target_w = 8 * cm  # width per cell
    target_h = 8 * cm  # max height per cell

    cells = []
    for img in images:
        try:
            raw = base64.b64decode(img["data"])
        except Exception:
            continue
        pil = PILImage.open(BytesIO(raw))
        # Scale to fit target box, preserving aspect ratio
        w, h = pil.size
        scale = min(target_w / w, target_h / h)
        rl_img = RLImage(BytesIO(raw), width=w * scale, height=h * scale)
        caption = Paragraph(
            f'<i>{(img.get("caption") or "").replace("&", "&amp;")}</i>',
            styles["small"],
        )
        cells.append([rl_img, caption])

    # Pack into rows of `max_per_row`
    for row_start in range(0, len(cells), max_per_row):
        chunk = cells[row_start:row_start + max_per_row]
        # Compose each cell as a sub-table (image stacked over caption)
        row_cells = []
        for img_flow, caption_flow in chunk:
            sub = Table([[img_flow], [caption_flow]], colWidths=[target_w])
            sub.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))
            row_cells.append(sub)
        # Pad the row to `max_per_row` columns so the table stays even
        while len(row_cells) < max_per_row:
            row_cells.append("")

        row_table = Table([row_cells], colWidths=[target_w] * max_per_row)
        row_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        flowables.append(row_table)
        flowables.append(Spacer(1, 6))

    return flowables


def generate_pdf(data: dict) -> bytes:
    """Render the tech pack as a PDF and return raw bytes."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title=f"Tech Pack — {data.get('style_name') or 'Untitled'}",
    )
    styles = _styles()
    story = []

    # --- Title block ---
    story.append(Paragraph("TECH PACK", styles["title"]))
    story.append(Paragraph(
        f"{data.get('style_name') or '—'} · {data.get('style_number') or '—'}"
        f" · {data.get('season') or '—'}",
        styles["normal"],
    ))
    story.append(Paragraph(
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        styles["small"],
    ))
    story.append(Spacer(1, 8))

    is_knitwear = (data.get("product_type") or "").startswith("Knitwear")

    # --- Images (rendered before the textual sections) ---
    images = data.get("images") or []
    if images:
        story.append(Paragraph("Images & References", styles["h2"]))
        story.extend(_render_images(images))

    # --- AI Technical Drawing (its own section) ---
    tech_drawing = data.get("technical_drawing")
    if tech_drawing:
        story.append(Paragraph("Technical Drawing (AI-generated)", styles["h2"]))
        story.extend(_render_images([tech_drawing], max_per_row=1))

    # --- 1. Style Overview ---
    story.append(Paragraph("1. Style Overview", styles["h2"]))
    story.append(_kv_table([
        ("Product Type", data.get("product_type")),
        ("Style Name", data.get("style_name")),
        ("Style Number", data.get("style_number")),
        ("Season", data.get("season")),
        ("Gender", data.get("gender")),
        ("Fit", data.get("fit")),
        ("Size Range", data.get("size_range")),
        ("Color", data.get("color_name")),
        ("Pantone Code", data.get("pantone_code")),
        ("Composition", data.get("composition")),
    ]))

    # --- 2. Material & Construction ---
    story.append(Paragraph("2. Material & Construction", styles["h2"]))
    if is_knitwear:
        story.append(_kv_table([
            ("Yarn Type", data.get("yarn_type")),
            ("Yarn Count", data.get("yarn_count")),
            ("Gauge", data.get("gauge")),
            ("Knit Structure", data.get("knit_structure")),
            ("Rib Structure", data.get("rib_structure")),
            ("Dyeing Method", data.get("dye_method")),
        ]))
    else:
        story.append(_kv_table([
            ("Fabric Structure", data.get("fabric_structure")),
            ("Fabric Weight (gsm)", data.get("fabric_weight_gsm")),
            ("Dyeing Method", data.get("dye_method")),
        ]))

    # --- 3. Style Details ---
    story.append(Paragraph("3. Style Details", styles["h2"]))
    details = [
        ("Neckline", f"{data.get('neckline') or '—'} (rib: {data.get('neckline_rib_cm') or 0} cm)"),
        ("Sleeve", f"{data.get('sleeve_length') or '—'} / {data.get('sleeve_type') or '—'}"),
        ("Hem", f"{data.get('hem_style') or '—'} ({data.get('hem_height_cm') or 0} cm)"),
        ("Cuff", f"{data.get('cuff_style') or '—'} ({data.get('cuff_height_cm') or 0} cm)"),
        ("Placket / Closure", data.get("placket")),
        ("Placket interlining", data.get("placket_interlining")),
    ]
    if "button" in (data.get("placket") or "").lower():
        details.append((
            "Buttons",
            f"{data.get('button_count') or 0} × {data.get('button_size_l') or '—'} "
            f"{data.get('button_material') or ''} ({data.get('button_color') or ''})",
        ))
    details.extend([
        ("Print / Embroidery", data.get("print_embroidery")),
        ("Wash / Finishing", data.get("wash_finishing")),
        ("Stitching / Construction", data.get("stitching_type")),
        ("Shoulder Reinforcement", "Yes" if data.get("shoulder_reinforcement") else "No"),
    ])
    story.append(_kv_table(details))

    # --- 4. Measurements ---
    story.append(Paragraph(f"4. Measurements (Size {data.get('base_size') or 'M'})", styles["h2"]))
    measurements = data.get("measurements") or {}
    if measurements:
        story.append(_measurement_table(measurements, data.get("base_size") or "M"))
    else:
        story.append(Paragraph("No measurements specified.", styles["normal"]))

    # --- 5. Labels & Packing ---
    story.append(Paragraph("5. Labels & Packing", styles["h2"]))
    story.append(_kv_table([
        ("Labels", ", ".join(data.get("labels") or []) or "—"),
        ("Packing", data.get("packing")),
    ]))

    # --- 6. Supplier Actions ---
    story.append(Paragraph("6. Supplier Actions Required", styles["h2"]))
    actions = data.get("supplier_actions") or []
    if actions:
        for a in actions:
            story.append(Paragraph(f"• {a}", styles["normal"]))
    else:
        story.append(Paragraph("None specified.", styles["normal"]))

    # --- 7. Commercial ---
    story.append(Paragraph("7. Commercial Information", styles["h2"]))
    story.append(_kv_table([
        ("Target Quantity", f"{data.get('target_quantity') or 0} pcs"),
        ("Target Price", f"${data.get('target_price_usd') or 0:.2f} USD"),
        ("Delivery Date", str(data.get("delivery_date") or "—")),
    ]))
    if data.get("notes"):
        story.append(Spacer(1, 6))
        story.append(Paragraph(f"<b>Notes:</b> {data['notes']}", styles["normal"]))

    doc.build(story)
    return buf.getvalue()
