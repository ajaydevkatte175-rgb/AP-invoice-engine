import argparse
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

VENDORS = [
    {
        "name": "Acme Industrial Supplies Ltd",
        "tax_id": "GB123456789",
        "address": "100 Enterprise Way, London, UK",
    },
    {
        "name": "Apex Cloud Technologies",
        "tax_id": "US987654321",
        "address": "456 Silicon Ave, San Jose, CA, USA",
    },
    {
        "name": "Nexus Logistics Global",
        "tax_id": "DE554433221",
        "address": "Hafenstrasse 12, Hamburg, Germany",
    },
]

CATALOG = [
    ("Precision Metal Fasteners Box 500", Decimal("2"), Decimal("45.00")),
    ("High-Grade Machine Lubricant 5L", Decimal("1"), Decimal("35.00")),
    ("Industrial Safety Goggles Pack 10", Decimal("3"), Decimal("15.00")),
    ("Cloud Server Compute Standard Node", Decimal("1"), Decimal("120.00")),
    ("Dedicated IPv4 Address Range Block", Decimal("4"), Decimal("25.00")),
    ("International Pallet Freight Handling", Decimal("1"), Decimal("350.00")),
]


def generate_single_invoice_pdf(
    output_path: Path,
    index: int,
    vendor: dict,
    items: list[tuple[str, Decimal, Decimal]],
) -> None:
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
    )
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "InvoiceTitle",
        parent=styles["Heading1"],
        fontSize=24,
        leading=28,
        textColor=colors.HexColor("#1e293b"),
    )
    bold_style = ParagraphStyle(
        "BoldText",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#334155"),
    )
    normal_style = ParagraphStyle(
        "RegularText",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#475569"),
    )

    story = []

    # Title
    story.append(Paragraph("<b>TAX INVOICE</b>", title_style))
    story.append(Spacer(1, 15))

    invoice_number = f"INV-2026-{index:04d}"
    inv_date = date(2026, 9, 1) + timedelta(days=index)
    due_date = inv_date + timedelta(days=30)

    # Header details table
    header_data = [
        [
            Paragraph(f"<b>Vendor:</b> {vendor['name']}", bold_style),
            Paragraph(f"<b>Invoice Number:</b> {invoice_number}", bold_style),
        ],
        [
            Paragraph(f"<b>Tax ID:</b> {vendor['tax_id']}", normal_style),
            Paragraph(f"<b>Invoice Date:</b> {inv_date.isoformat()}", normal_style),
        ],
        [
            Paragraph(f"<b>Address:</b> {vendor['address']}", normal_style),
            Paragraph(f"<b>Due Date:</b> {due_date.isoformat()}", normal_style),
        ],
        [
            Paragraph("<b>Billed To:</b> Enterprise Client Corp", normal_style),
            Paragraph("<b>Currency:</b> USD", normal_style),
        ],
    ]

    header_table = Table(header_data, colWidths=[270, 270])
    header_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(header_table)
    story.append(Spacer(1, 20))

    # Line Items Table
    table_data = [
        [
            Paragraph("<b>#</b>", bold_style),
            Paragraph("<b>Description</b>", bold_style),
            Paragraph("<b>Qty</b>", bold_style),
            Paragraph("<b>Unit Price</b>", bold_style),
            Paragraph("<b>Line Total</b>", bold_style),
        ]
    ]

    subtotal = Decimal("0.00")
    for idx, (desc, qty, unit_price) in enumerate(items, start=1):
        line_total = (qty * unit_price).quantize(Decimal("0.01"))
        subtotal += line_total
        # Pipe-delimited plain text pattern included for robust extraction
        desc_text = f"{desc} | Qty: {qty} | Price: {unit_price:.2f} | Total: {line_total:.2f}"
        table_data.append(
            [
                str(idx),
                Paragraph(desc_text, normal_style),
                f"{qty:.4f}",
                f"{unit_price:.2f}",
                f"{line_total:.2f}",
            ]
        )

    tax_rate = Decimal("0.20")  # 20%
    tax_amount = (subtotal * tax_rate).quantize(Decimal("0.01"))
    total_amount = subtotal + tax_amount

    # Totals rows
    table_data.append(["", "", "", Paragraph("<b>Subtotal:</b>", bold_style), f"{subtotal:.2f}"])
    table_data.append(["", "", "", Paragraph("<b>Tax (20%):</b>", bold_style), f"{tax_amount:.2f}"])
    table_data.append(["", "", "", Paragraph("<b>Total:</b>", bold_style), f"{total_amount:.2f}"])

    item_table = Table(table_data, colWidths=[30, 260, 70, 90, 90])
    item_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, len(items)), 0.5, colors.HexColor("#cbd5e1")),
                ("LINEBELOW", (0, 0), (-1, 0), 1.5, colors.HexColor("#64748b")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(item_table)
    story.append(Spacer(1, 20))

    # Plain text summary block
    summary_text = (
        f"Subtotal: {subtotal:.2f} USD\n"
        f"Tax Amount: {tax_amount:.2f} USD\n"
        f"Total Amount: {total_amount:.2f} USD\n"
        f"Generated: {datetime.now().isoformat()}\n"
    )
    story.append(Paragraph(f"<font size=8 color='#64748b'>{summary_text}</font>", normal_style))

    doc.build(story)


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic evaluation invoices in PDF format")
    parser.add_argument("--count", type=int, default=3, help="Number of invoices to generate")
    parser.add_argument("--output-dir", type=str, default="data/eval/pdfs", help="Directory to save PDFs")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Generating {args.count} invoices into {out_dir.resolve()}...")

    for i in range(args.count):
        vendor = VENDORS[i % len(VENDORS)]
        items_slice = CATALOG[i % 3 : (i % 3) + 3]
        if not items_slice:
            items_slice = CATALOG[:3]

        file_path = out_dir / f"invoice_{i:04d}.pdf"
        generate_single_invoice_pdf(file_path, i, vendor, items_slice)
        print(f"  Generated: {file_path}")

    print("All invoices successfully generated.")


if __name__ == "__main__":
    main()

