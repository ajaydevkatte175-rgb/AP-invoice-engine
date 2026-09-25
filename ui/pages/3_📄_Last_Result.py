"""Last Result - Detailed view of the most recently processed invoice document."""

from decimal import Decimal

import pandas as pd
import streamlit as st

from ui.lib.api_client import APIClient
from ui.lib.formatters import format_currency, render_confidence_metric, render_status_pill

st.set_page_config(page_title="Last Result | AP Invoice Engine", page_icon="📄", layout="wide")

st.title("📄 Last Extraction Result")
st.markdown(
    "Detailed breakdown of the most recently ingested document, its extracted fields, and deterministic validation flags."
)

client = APIClient()

latest = None
try:
    latest = client.get_latest_document()
except Exception as e:
    st.error(f"Failed to fetch latest document: {e}")

if not latest or not latest.get("document"):
    st.info(
        "No documents have been processed yet. Go to Scan & Upload to process your first invoice."
    )
    if st.button("📸 Go to Scan & Upload", type="primary"):
        st.switch_page("pages/1_📸_Scan_Upload.py")
    st.stop()

doc = latest["document"]
inv = latest.get("invoice")
items = latest.get("line_items", [])
flags = latest.get("validation_flags", [])

# Top Summary Banner
with st.container():
    c_info, c_status, c_conf = st.columns([2, 1, 1])
    with c_info:
        st.subheader(f"📁 {doc.get('filename')}")
        st.caption(
            f"Document ID: `{doc.get('id')}` • SHA-256: `{str(doc.get('sha256_hash', ''))[:16]}...`"
        )
    with c_status:
        st.write("**Document Status:**")
        render_status_pill(doc.get("status", "completed"))
    with c_conf:
        if inv:
            st.write("**Extraction Confidence:**")
            render_confidence_metric(inv.get("confidence_score"))

st.markdown("---")

if not inv:
    st.warning("Invoice data is currently being extracted in the background worker pipeline...")
    st.stop()

# Invoice Header Details
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.write("**Supplier / Vendor:**")
    st.markdown(f"### {inv.get('vendor_name') or 'N/A'}")
    st.write("**Invoice Number:**")
    st.markdown(f"`{inv.get('invoice_number') or 'N/A'}`")

with col2:
    st.write("**Invoice Date:**", inv.get("invoice_date") or "N/A")
    st.write("**Due Date:**", inv.get("due_date") or "N/A")
    st.write("**Currency:**", inv.get("currency") or "USD")

with col3:
    curr = inv.get("currency", "USD")
    st.metric("Subtotal", format_currency(inv.get("subtotal"), curr))
    st.metric("Tax Amount", format_currency(inv.get("tax_amount"), curr))

with col4:
    curr = inv.get("currency", "USD")
    st.metric("Total Amount", format_currency(inv.get("total_amount"), curr))
    st.write("**Invoice Status:**")
    render_status_pill(inv.get("status", "completed"))

st.markdown("---")

# Deterministic Arithmetic Verification Panel
st.subheader("🧮 Deterministic Arithmetic Check")

subtotal_str = inv.get("subtotal")
tax_str = inv.get("tax_amount")
total_str = inv.get("total_amount")

math_ok = True
math_msg = ""

try:
    if subtotal_str is not None and tax_str is not None and total_str is not None:
        dec_sub = Decimal(str(subtotal_str))
        dec_tax = Decimal(str(tax_str))
        dec_tot = Decimal(str(total_str))
        calc_tot = dec_sub + dec_tax
        if abs(calc_tot - dec_tot) < Decimal("0.01"):
            math_ok = True
            math_msg = f"Pure Decimal verification confirmed: {format_currency(dec_sub, curr)} (Subtotal) + {format_currency(dec_tax, curr)} (Tax) = {format_currency(dec_tot, curr)} (Total)"
        else:
            math_ok = False
            math_msg = f"Discrepancy detected: Subtotal ({dec_sub}) + Tax ({dec_tax}) = {calc_tot} {curr}, but printed Total is {dec_tot} {curr} (Difference: {dec_tot - calc_tot})"
    else:
        math_msg = "Subtotal or Tax not specified on invoice."
except Exception as ex:
    math_ok = False
    math_msg = f"Arithmetic verification error: {ex}"

if math_ok:
    st.success(f"✅ **Arithmetic Consistency Confirmed:** {math_msg}")
else:
    st.error(f"⚠️ **Arithmetic Error Detected:** {math_msg}")

st.markdown("---")

# Line Items Section
st.subheader(f"📦 Line Items ({len(items)})")
if items:
    items_df = pd.DataFrame(items)
    cols = [
        c
        for c in [
            "line_number",
            "description",
            "quantity",
            "unit_price",
            "line_total",
            "confidence",
        ]
        if c in items_df.columns
    ]
    st.dataframe(items_df[cols], use_container_width=True, hide_index=True)
else:
    st.info("No line items extracted for this invoice.")

# Validation Flags Section
st.subheader("🚩 Validation Engine Flags")
if flags:
    for f in flags:
        sev = f.get("severity", "WARNING").upper()
        rule = f.get("flag_type", "Rule")
        msg = f.get("message", "")
        if sev in ("ERROR", "CRITICAL"):
            st.error(f"🔴 **[{sev}] {rule}:** {msg}")
        else:
            st.warning(f"🟡 **[{sev}] {rule}:** {msg}")
else:
    st.success("🟢 No validation flags or errors raised. Invoice is clean.")

st.markdown("---")
# Quick Actions
act_col1, act_col2 = st.columns(2)
with act_col1:
    if inv.get("status") == "needs_review" or flags:
        if st.button("✏️ Review in Human Queue", type="primary", use_container_width=True):
            st.switch_page("pages/5_✅_Review_Queue.py")
with act_col2:
    if st.button("📸 Scan Another Invoice", use_container_width=True):
        st.switch_page("pages/1_📸_Scan_Upload.py")
