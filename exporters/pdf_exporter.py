"""
Generate a printable PDF version of the tech pack using reportlab.
"""

from datetime import datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


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
