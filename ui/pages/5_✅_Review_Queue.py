"""Review Queue - Side-by-side human review UI with live arithmetic validation."""

from decimal import Decimal

import pandas as pd
import streamlit as st

from ui.lib.api_client import APIClient
from ui.lib.formatters import format_currency

st.set_page_config(page_title="Review Queue | AP Invoice Engine", page_icon="✅", layout="wide")

st.title("✅ Human Review Queue")
st.markdown(
    "Invoices routed for human verification due to validation flags, arithmetic mismatches, or low extraction confidence. "
    "Compare the original document against extracted values, correct any errors, and verify the math live."
)

client = APIClient()

# Load invoices needing review
review_invoices = []
try:
    res = client.get_review_queue(limit=50)
    review_invoices = res.get("items", [])
except Exception as e:
    st.error(f"Error fetching review queue: {e}")

if not review_invoices:
    st.success("🎉 All clear! There are currently no invoices in the review queue.")
    st.markdown("All inbound invoices have either passed automated validation or been verified.")
    if st.button("📚 View All Invoices"):
        st.switch_page("pages/2_📚_Invoices.py")
    st.stop()

st.info(f"📌 **{len(review_invoices)}** invoice(s) currently awaiting human review.")

options = {
    f"{inv.get('invoice_number', 'N/A')} - {inv.get('vendor_name', 'Unknown')} ({format_currency(inv.get('total_amount'), inv.get('currency', 'USD'))})": inv
    for inv in review_invoices
}

selected_label = st.selectbox("Select invoice to review:", list(options.keys()))
active_inv_summary = options[selected_label]
invoice_id = active_inv_summary["id"]

# Fetch detailed invoice data
active_inv = None
try:
    active_inv = client.get_invoice(invoice_id)
except Exception as ex:
    st.error(f"Failed to load invoice details: {ex}")
    st.stop()

header = active_inv
line_items_data = active_inv.get("line_items", [])
flags = active_inv.get("validation_flags", [])
document_id = active_inv.get("document_id")

col_left, col_right = st.columns([1, 1], gap="medium")

# -----------------------------------------------------------------------------
# LEFT COLUMN: Original Document & Flags
# -----------------------------------------------------------------------------
with col_left:
    st.subheader("📄 Original Document Scan")

    # Fetch document binary
    doc_bytes = None
    if document_id:
        try:
            doc_bytes = client.get_document_file(document_id)
        except Exception:
            doc_bytes = None

    if doc_bytes:
        # Check if image or PDF
        is_pdf = doc_bytes.startswith(b"%PDF")
        if is_pdf:
            st.info("📑 Multi-page PDF Document")
            st.download_button(
                label="📥 Download & View Original PDF",
                data=doc_bytes,
                file_name=f"document_{document_id}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
            # Display PDF embed or fallback frame
            st.caption("PDF preview available via download button above.")
        else:
            st.image(doc_bytes, caption="Scanned Document Image", use_container_width=True)
    else:
        st.warning("Original document file preview is unavailable or stored offline.")
        st.markdown(
            f"""
            <div style="border: 2px dashed #cbd5e1; border-radius: 8px; padding: 40px; text-align: center; color: #64748b;">
                <h4>Document Preview</h4>
                <p>File reference: {active_inv.get("document_id")}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("---")
    st.subheader("🚩 Flags Requiring Review")
    if flags:
        for f in flags:
            sev = f.get("severity", "WARNING").upper()
            rule = f.get("rule_name", f.get("flag_type", "Rule"))
            msg = f.get("message", "")
            if sev in ("ERROR", "CRITICAL"):
                st.error(f"🔴 **[{sev}] {rule}:** {msg}")
            else:
                st.warning(f"🟡 **[{sev}] {rule}:** {msg}")
    else:
        st.info("No specific flags listed.")

# -----------------------------------------------------------------------------
# RIGHT COLUMN: Editable Form & Live Math Check
# -----------------------------------------------------------------------------
with col_right:
    st.subheader("✏️ Correct & Verify Invoice Data")

    with st.form("review_form"):
        r1_c1, r1_c2 = st.columns(2)
        with r1_c1:
            vendor_name = st.text_input(
                "Vendor / Supplier Name", value=header.get("vendor_name") or ""
            )
        with r1_c2:
            invoice_number = st.text_input(
                "Invoice Number", value=header.get("invoice_number") or ""
            )

        r2_c1, r2_c2, r2_c3 = st.columns(3)
        with r2_c1:
            invoice_date = st.text_input(
                "Invoice Date (YYYY-MM-DD)", value=str(header.get("invoice_date") or "")
            )
        with r2_c2:
            due_date = st.text_input(
                "Due Date (YYYY-MM-DD)", value=str(header.get("due_date") or "")
            )
        with r2_c3:
            currency = st.text_input("Currency", value=header.get("currency") or "USD")

        r3_c1, r3_c2, r3_c3 = st.columns(3)
        with r3_c1:
            subtotal_in = st.text_input("Subtotal", value=str(header.get("subtotal") or "0.00"))
        with r3_c2:
            tax_in = st.text_input("Tax Amount", value=str(header.get("tax_amount") or "0.00"))
        with r3_c3:
            total_in = st.text_input(
                "Total Amount", value=str(header.get("total_amount") or "0.00")
            )

        st.markdown("##### Line Items")
        st.caption(
            "Review extracted line items. You can edit line descriptions, quantities, and prices below:"
        )

        # Prepare line items for editor
        table_rows = []
        for li in line_items_data:
            table_rows.append(
                {
                    "Description": li.get("description", ""),
                    "Quantity": float(li.get("quantity", 1)),
                    "Unit Price": float(li.get("unit_price", 0.0)),
                    "Line Total": float(li.get("total_amount", li.get("line_total", 0.0))),
                }
            )

        if not table_rows:
            table_rows = [
                {
                    "Description": "Item 1",
                    "Quantity": 1.0,
                    "Unit Price": float(header.get("total_amount") or 0.0),
                    "Line Total": float(header.get("total_amount") or 0.0),
                }
            ]

        edited_df = st.data_editor(
            pd.DataFrame(table_rows),
            num_rows="dynamic",
            use_container_width=True,
            key="review_line_items_editor",
        )

        st.markdown("---")

        # Live Math Check Panel
        calc_line_sum = Decimal("0.00")
        try:
            for _, row in edited_df.iterrows():
                q = Decimal(str(row.get("Quantity", 0)))
                p = Decimal(str(row.get("Unit Price", 0)))
                calc_line_sum += q * p
        except Exception:
            pass

        try:
            dec_sub = Decimal(str(subtotal_in))
            dec_tax = Decimal(str(tax_in))
            dec_tot = Decimal(str(total_in))
            expected_tot = dec_sub + dec_tax
            diff = abs(expected_tot - dec_tot)
            is_math_balanced = diff < Decimal("0.01")
        except Exception:
            is_math_balanced = False
            dec_sub, dec_tax, dec_tot, expected_tot = Decimal(0), Decimal(0), Decimal(0), Decimal(0)

        if is_math_balanced:
            st.success(
                f"🟢 **Live Math Verified!**\n\n"
                f"Subtotal ({format_currency(dec_sub, currency)}) + Tax ({format_currency(dec_tax, currency)}) "
                f"= **{format_currency(dec_tot, currency)}** (Total). Numbers are consistent!"
            )
        else:
            st.error(
                f"🔴 **Arithmetic Discrepancy:**\n\n"
                f"Subtotal ({dec_sub}) + Tax ({dec_tax}) = **{expected_tot}**, but entered Total is **{dec_tot}** "
                f"(Discrepancy: {dec_tot - expected_tot} {currency}).\n\n"
                f"Computed Line Items Sum: **{format_currency(calc_line_sum, currency)}**"
            )

        st.markdown("---")
        submitted = st.form_submit_button(
            "✅ Approve & Mark Completed", type="primary", use_container_width=True
        )

        if submitted:
            try:
                update_payload = {
                    "vendor_name": vendor_name.strip(),
                    "invoice_number": invoice_number.strip(),
                    "currency": currency.strip().upper(),
                    "subtotal": str(dec_sub),
                    "tax_amount": str(dec_tax),
                    "total_amount": str(dec_tot),
                }
                if invoice_date.strip():
                    update_payload["invoice_date"] = invoice_date.strip()
                if due_date.strip():
                    update_payload["due_date"] = due_date.strip()

                client.update_invoice(invoice_id, update_payload)
                client.update_invoice_status(invoice_id, "completed")
                st.success("🎉 Invoice approved and marked as Completed!")
                st.rerun()
            except Exception as ex:
                st.error(f"Failed to update invoice: {ex}")
