"""Invoices Directory - Searchable, filterable tables for Inbound (AP) and Outbound (AR) invoices."""

import pandas as pd
import streamlit as st

from ui.lib.api_client import APIClient
from ui.lib.formatters import format_currency, render_confidence_metric, render_status_pill

st.set_page_config(
    page_title="Invoices Directory | AP Invoice Engine", page_icon="📚", layout="wide"
)

st.title("📚 Invoices Directory")
st.markdown(
    "Search, filter, and inspect full server-side historical records for Accounts Payable and Accounts Receivable."
)

client = APIClient()

tab_inbound, tab_outbound = st.tabs(
    ["📥 Inbound (AP) Invoices", "📤 Outbound (AR) Issued Invoices"]
)

# -----------------------------------------------------------------------------
# TAB 1: Inbound (AP) Invoices
# -----------------------------------------------------------------------------
with tab_inbound:
    st.subheader("Supplier Invoices (Accounts Payable)")

    col_search, col_status = st.columns([2, 1])
    with col_search:
        search_query = st.text_input(
            "🔍 Search by Vendor or Invoice #",
            placeholder="e.g. Acme, INV-2024...",
            key="ap_search",
        )
    with col_status:
        status_choice = st.selectbox(
            "Filter by Status",
            options=["All", "completed", "needs_review", "pending_validation", "void"],
            key="ap_status",
        )

    filter_status = None if status_choice == "All" else status_choice
    filter_search = search_query.strip() if search_query.strip() else None

    invoices_data = []
    total_count = 0
    try:
        res = client.list_invoices(status=filter_status, search=filter_search, limit=100)
        invoices_data = res.get("items", [])
        total_count = res.get("total", len(invoices_data))
    except Exception as e:
        st.error(f"Error loading inbound invoices: {e}")

    st.caption(f"Showing **{len(invoices_data)}** of **{total_count}** invoices")

    if not invoices_data:
        st.info("No inbound invoices found matching the current filters.")
    else:
        # Build tabular display
        table_rows = []
        for inv in invoices_data:
            table_rows.append(
                {
                    "ID": inv.get("id"),
                    "Invoice #": inv.get("invoice_number") or "N/A",
                    "Vendor": inv.get("vendor_name") or "Unknown",
                    "Date": str(inv.get("invoice_date") or ""),
                    "Due Date": str(inv.get("due_date") or ""),
                    "Total": format_currency(inv.get("total_amount"), inv.get("currency", "USD")),
                    "Status": inv.get("status", ""),
                    "Confidence": f"{float(inv.get('extraction_confidence', 0) or 0) * 100:.1f}%",
                }
            )

        df = pd.DataFrame(table_rows)
        st.dataframe(
            df[["Invoice #", "Vendor", "Date", "Due Date", "Total", "Status", "Confidence"]],
            use_container_width=True,
            hide_index=True,
        )

        st.markdown("---")
        st.subheader("🔍 Inspect Invoice Details")
        inv_options = {
            f"{r['Invoice #']} - {r['Vendor']} ({r['Total']})": r["ID"] for r in table_rows
        }
        selected_label = st.selectbox(
            "Select an invoice to inspect line items and flags:", list(inv_options.keys())
        )

        if selected_label:
            selected_id = inv_options[selected_label]
            try:
                inv_detail = client.get_invoice(selected_id)
                header = inv_detail
                items = inv_detail.get("line_items", [])
                flags = inv_detail.get("validation_flags", [])

                h1, h2, h3, h4 = st.columns(4)
                with h1:
                    st.write("**Vendor:**", header.get("vendor_name"))
                    st.write("**Invoice #:**", header.get("invoice_number"))
                with h2:
                    st.write("**Date:**", header.get("invoice_date"))
                    st.write("**Due Date:**", header.get("due_date"))
                with h3:
                    st.write(
                        "**Subtotal:**",
                        format_currency(header.get("subtotal"), header.get("currency", "USD")),
                    )
                    st.write(
                        "**Tax:**",
                        format_currency(header.get("tax_amount"), header.get("currency", "USD")),
                    )
                    st.write(
                        "**Total:**",
                        format_currency(header.get("total_amount"), header.get("currency", "USD")),
                    )
                with h4:
                    st.write("**Status:**")
                    render_status_pill(header.get("status", ""))
                    st.write("**Confidence:**")
                    render_confidence_metric(header.get("extraction_confidence"))

                if items:
                    st.markdown("##### Line Items")
                    items_df = pd.DataFrame(items)
                    display_cols = [
                        c
                        for c in [
                            "line_number",
                            "description",
                            "quantity",
                            "unit_price",
                            "total_amount",
                            "confidence",
                        ]
                        if c in items_df.columns
                    ]
                    st.dataframe(items_df[display_cols], use_container_width=True, hide_index=True)

                if flags:
                    st.markdown("##### Validation Flags")
                    for flg in flags:
                        sev = flg.get("severity", "WARNING").upper()
                        msg = f"**[{sev}] {flg.get('rule_name', flg.get('flag_type', 'Flag'))}:** {flg.get('message')}"
                        if sev in ("ERROR", "CRITICAL"):
                            st.error(msg)
                        else:
                            st.warning(msg)
            except Exception as ex:
                st.error(f"Failed to fetch invoice details: {ex}")

# -----------------------------------------------------------------------------
# TAB 2: Outbound (AR) Issued Invoices
# -----------------------------------------------------------------------------
with tab_outbound:
    st.subheader("Issued Invoices (Accounts Receivable)")

    col_out_status, col_out_action = st.columns([1, 2])
    with col_out_status:
        ar_status_choice = st.selectbox(
            "Filter by Outbound Status",
            options=["All", "draft", "issued", "paid", "cancelled"],
            key="ar_status",
        )
    with col_out_action:
        st.write("")
        st.write("")
        if st.button("➕ Create New Outbound Invoice", type="primary"):
            st.switch_page("pages/6_🧾_Create_Invoice.py")

    filter_ar_status = None if ar_status_choice == "All" else ar_status_choice

    outbound_data = []
    try:
        res = client.list_issued_invoices(status=filter_ar_status, limit=100)
        outbound_data = res.get("items", [])
    except Exception as e:
        st.error(f"Error loading issued invoices: {e}")

    if not outbound_data:
        st.info("No outbound issued invoices found.")
    else:
        out_table = []
        for inv in outbound_data:
            out_table.append(
                {
                    "ID": inv.get("id"),
                    "Invoice #": inv.get("invoice_number"),
                    "Customer ID": str(inv.get("customer_id") or "")[:8] + "...",
                    "Issue Date": str(inv.get("issue_date") or ""),
                    "Due Date": str(inv.get("due_date") or ""),
                    "Subtotal": format_currency(inv.get("subtotal"), inv.get("currency", "USD")),
                    "Tax": format_currency(inv.get("tax_amount"), inv.get("currency", "USD")),
                    "Total": format_currency(inv.get("total_amount"), inv.get("currency", "USD")),
                    "Status": inv.get("status", ""),
                }
            )

        df_out = pd.DataFrame(out_table)
        st.dataframe(
            df_out[["Invoice #", "Issue Date", "Due Date", "Subtotal", "Tax", "Total", "Status"]],
            use_container_width=True,
            hide_index=True,
        )

        st.markdown("---")
        st.subheader("📥 Download Invoice PDF & Manage Status")
        ar_options = {
            f"{r['Invoice #']} - {r['Total']} ({r['Status']})": r["ID"] for r in out_table
        }
        selected_ar_label = st.selectbox("Select an issued invoice:", list(ar_options.keys()))

        if selected_ar_label:
            selected_ar_id = ar_options[selected_ar_label]
            inv_obj = next(
                (x for x in outbound_data if str(x.get("id")) == str(selected_ar_id)), None
            )

            ar_c1, ar_c2, ar_c3 = st.columns([1, 1, 1])
            with ar_c1:
                try:
                    pdf_bytes = client.get_invoice_pdf(selected_ar_id)
                    st.download_button(
                        label=f"📄 Download PDF ({inv_obj.get('invoice_number') if inv_obj else 'invoice'})",
                        data=pdf_bytes,
                        file_name=f"{inv_obj.get('invoice_number', 'invoice')}.pdf",
                        mime="application/pdf",
                        use_container_width=True,
                    )
                except Exception as ex:
                    st.error(f"Failed to generate PDF: {ex}")

            with ar_c2:
                current_st = inv_obj.get("status") if inv_obj else "draft"
                if current_st == "draft":
                    if st.button("📤 Mark as Issued", use_container_width=True):
                        client.update_issued_invoice_status(selected_ar_id, "issued")
                        st.success("Invoice status updated to Issued!")
                        st.rerun()
                elif current_st == "issued":
                    if st.button("💰 Mark as Paid", use_container_width=True):
                        client.update_issued_invoice_status(selected_ar_id, "paid")
                        st.success("Invoice status updated to Paid!")
                        st.rerun()

            with ar_c3:
                if inv_obj and inv_obj.get("status") not in ("paid", "cancelled"):
                    if st.button("❌ Cancel Invoice", use_container_width=True):
                        client.update_issued_invoice_status(selected_ar_id, "cancelled")
                        st.warning("Invoice cancelled.")
                        st.rerun()
