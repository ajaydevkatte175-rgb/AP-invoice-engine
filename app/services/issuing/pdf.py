"""A4 PDF invoice layout generator using ReportLab.

Produces professional, high-fidelity A4 PDF documents for issued outbound invoices.
"""

import io
from datetime import date
from decimal import Decimal
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def generate_invoice_pdf(
    invoice_data: dict[str, Any],
    company_profile: dict[str, Any] | None = None,
    customer_data: dict[str, Any] | None = None,
) -> bytes:
    """Generate an A4 PDF for an issued invoice and return bytes.

    Args:
        invoice_data: Dictionary containing invoice header & line items:
            - invoice_number: str
            - invoice_date: date | str
            - due_date: date | str
            - currency: str
            - subtotal: Decimal | float | str
            - tax_rate: Decimal | float | str
            - tax_amount: Decimal | float | str
            - total_amount: Decimal | float | str
            - status: str
            - notes: str | None
            - payment_terms: str | None
            - line_items: list of dicts (line_number, description, quantity, unit_price, total_amount)
        company_profile: Optional tenant profile information:
            - company_name, address, tax_id, email, phone, bank_details
        customer_data: Optional customer recipient details:
            - name, address, tax_id, email, phone
    """
    buffer = io.BytesIO()

    # Document setup: A4 with 15mm margins
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
    )

    styles = getSampleStyleSheet()

    # Palette
    primary_color = colors.HexColor("#1e293b")  # slate-800
    brand_accent = colors.HexColor("#2563eb")   # blue-600
    text_muted = colors.HexColor("#64748b")     # slate-500
    border_color = colors.HexColor("#e2e8f0")   # slate-200
    bg_subtle = colors.HexColor("#f8fafc")      # slate-50

    # Custom styles
    normal_style = ParagraphStyle(
        "NormalText",
        parent=styles["Normal"],
        fontSize=9,
        leading=12,
        textColor=primary_color,
        fontName="Helvetica",
    )
    muted_style = ParagraphStyle(
        "MutedText",
        parent=styles["Normal"],
        fontSize=8,
        leading=11,
        textColor=text_muted,
        fontName="Helvetica",
    )
    bold_style = ParagraphStyle(
        "BoldText",
        parent=styles["Normal"],
        fontSize=9,
        leading=12,
        textColor=primary_color,
        fontName="Helvetica-Bold",
    )

    story = []

    # 1. Header: Seller info on left, Invoice title & number on right
    co = company_profile or {}
    company_name = co.get("company_name", "Acme Corporation")
    co_addr = co.get("address", "") or ""
    co_tax = co.get("tax_id", "") or ""
    co_email = co.get("email", "") or ""
    co_phone = co.get("phone", "") or ""

    inv_num = invoice_data.get("invoice_number", "INV-DRAFT")
    inv_date = str(invoice_data.get("invoice_date", date.today()))
    due_date = str(invoice_data.get("due_date", date.today()))
    currency = invoice_data.get("currency", "USD")
    status = str(invoice_data.get("status", "draft")).upper()

    company_info_text = f"<b>{company_name}</b><br/>"
    if co_addr:
        company_info_text += f"{co_addr.replace('\n', '<br/>')}<br/>"
    if co_tax:
        company_info_text += f"Tax ID: {co_tax}<br/>"
    if co_email:
        company_info_text += f"Email: {co_email}<br/>"
    if co_phone:
        company_info_text += f"Phone: {co_phone}"

    invoice_meta_text = (
        f"<font size=20 color='#2563eb'><b>INVOICE</b></font><br/><br/>"
        f"<b>Invoice #:</b> {inv_num}<br/>"
        f"<b>Date:</b> {inv_date}<br/>"
        f"<b>Due Date:</b> {due_date}<br/>"
        f"<b>Status:</b> {status}"
    )

    header_table_data = [
        [
            Paragraph(company_info_text, normal_style),
            Paragraph(invoice_meta_text, ParagraphStyle("RightMeta", parent=normal_style, alignment=2)),
        ]
    ]
    header_table = Table(header_table_data, colWidths=[90 * mm, 90 * mm])
    header_table.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
        ])
    )
    story.append(header_table)
    story.append(Spacer(1, 15 * mm))

    # 2. Bill To & Payment Info Block
    cust = customer_data or {}
    cust_name = cust.get("name", "Customer Name")
    cust_addr = cust.get("address", "") or ""
    cust_tax = cust.get("tax_id", "") or ""
    cust_email = cust.get("email", "") or ""

    bill_to_text = f"<b>BILL TO:</b><br/><b>{cust_name}</b><br/>"
    if cust_addr:
        bill_to_text += f"{cust_addr.replace('\n', '<br/>')}<br/>"
    if cust_tax:
        bill_to_text += f"Tax ID: {cust_tax}<br/>"
    if cust_email:
        bill_to_text += f"Email: {cust_email}"

    pay_terms = invoice_data.get("payment_terms", "Net 30") or "Net 30"
    bank = co.get("bank_details", {}) or {}
    bank_text = f"<b>PAYMENT TERMS:</b><br/>{pay_terms}<br/><br/>"
    if isinstance(bank, dict) and bank:
        bank_lines = [f"<b>{k.replace('_', ' ').title()}:</b> {v}" for k, v in bank.items()]
        bank_text += "<b>BANK DETAILS:</b><br/>" + "<br/>".join(bank_lines)

    info_table_data = [
        [
            Paragraph(bill_to_text, normal_style),
            Paragraph(bank_text, normal_style),
        ]
    ]
    info_table = Table(info_table_data, colWidths=[100 * mm, 80 * mm])
    info_table.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, -1), bg_subtle),
            ("BOX", (0, 0), (-1, -1), 0.5, border_color),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ])
    )
    story.append(info_table)
    story.append(Spacer(1, 10 * mm))

    # 3. Line Items Table
    raw_items = invoice_data.get("line_items", [])
    table_rows = [
        [
            Paragraph("<b>#</b>", bold_style),
            Paragraph("<b>Description</b>", bold_style),
            Paragraph("<b>Qty</b>", ParagraphStyle("RBold", parent=bold_style, alignment=2)),
            Paragraph("<b>Unit Price</b>", ParagraphStyle("RBold", parent=bold_style, alignment=2)),
            Paragraph("<b>Total</b>", ParagraphStyle("RBold", parent=bold_style, alignment=2)),
        ]
    ]

    for idx, item in enumerate(raw_items, start=1):
        if isinstance(item, dict):
            num = item.get("line_number", idx)
            desc = item.get("description", "")
            qty = item.get("quantity", 1)
            unit_price = item.get("unit_price", 0)
            total = item.get("total_amount", 0)
        else:
            num = getattr(item, "line_number", idx)
            desc = getattr(item, "description", "")
            qty = getattr(item, "quantity", 1)
            unit_price = getattr(item, "unit_price", 0)
            total = getattr(item, "total_amount", 0)

        table_rows.append([
            Paragraph(str(num), normal_style),
            Paragraph(str(desc), normal_style),
            Paragraph(f"{Decimal(str(qty)):.2f}", ParagraphStyle("RNorm", parent=normal_style, alignment=2)),
            Paragraph(f"{Decimal(str(unit_price)):,.2f} {currency}", ParagraphStyle("RNorm", parent=normal_style, alignment=2)),
            Paragraph(f"{Decimal(str(total)):,.2f} {currency}", ParagraphStyle("RNorm", parent=normal_style, alignment=2)),
        ])

    items_table = Table(table_rows, colWidths=[12 * mm, 88 * mm, 22 * mm, 30 * mm, 28 * mm])
    items_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), bg_subtle),
            ("LINEBELOW", (0, 0), (-1, 0), 1.5, brand_accent),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("LINEBELOW", (0, 1), (-1, -1), 0.5, border_color),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ])
    )
    story.append(items_table)
    story.append(Spacer(1, 8 * mm))

    # 4. Totals Calculation Box
    subtotal = Decimal(str(invoice_data.get("subtotal", 0)))
    tax_rate = Decimal(str(invoice_data.get("tax_rate", 0)))
    tax_amount = Decimal(str(invoice_data.get("tax_amount", 0)))
    total_amount = Decimal(str(invoice_data.get("total_amount", 0)))

    totals_data = [
        [
            Paragraph("<b>Subtotal:</b>", ParagraphStyle("TotLabel", parent=normal_style, alignment=2)),
            Paragraph(f"{subtotal:,.2f} {currency}", ParagraphStyle("TotVal", parent=normal_style, alignment=2)),
        ],
        [
            Paragraph(f"<b>Tax ({tax_rate * 100:.1f}%):</b>", ParagraphStyle("TotLabel", parent=normal_style, alignment=2)),
            Paragraph(f"{tax_amount:,.2f} {currency}", ParagraphStyle("TotVal", parent=normal_style, alignment=2)),
        ],
        [
            Paragraph("<b>TOTAL DUE:</b>", ParagraphStyle("TotDueLabel", parent=bold_style, alignment=2, textColor=brand_accent)),
            Paragraph(f"<b>{total_amount:,.2f} {currency}</b>", ParagraphStyle("TotDueVal", parent=bold_style, alignment=2, textColor=brand_accent)),
        ],
    ]
    totals_table = Table(totals_data, colWidths=[40 * mm, 35 * mm])
    totals_table.setStyle(
        TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LINEBELOW", (0, 1), (-1, 1), 0.5, border_color),
            ("LINEBELOW", (0, 2), (-1, 2), 1.5, brand_accent),
        ])
    )

    # Place notes on left, totals on right
    notes = invoice_data.get("notes", "") or ""
    notes_paragraph = Paragraph(f"<b>Notes:</b><br/>{notes}", muted_style) if notes else Paragraph("", normal_style)

    bottom_block = Table([[notes_paragraph, totals_table]], colWidths=[105 * mm, 75 * mm])
    bottom_block.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ])
    )
    story.append(bottom_block)

    # Build PDF
    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes

