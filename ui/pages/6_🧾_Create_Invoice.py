"""Create Invoice - Outbound AR invoice drafting with natural language assistance and pure Decimal totals."""

from datetime import date, timedelta
from decimal import Decimal
import pandas as pd
import streamlit as st

from app.services.issuing.totals import compute_totals
from ui.lib.api_client import APIClient
from ui.lib.formatters import format_currency

st.set_page_config(page_title="Create Outbound Invoice | AP Invoice Engine", page_icon="🧾", layout="wide")

st.title("🧾 Create Outbound Invoice (AR)")
st.markdown(
    "Draft customer invoices manually or with natural language AI. "
    "**All arithmetic is strictly computed in Python using pure Decimal precision (Rule 4), with gapless sequential numbering (Rule 5).**"
)

client = APIClient()

# Initialize session state for draft fields if not present
if "draft_items" not in st.session_state:
    st.session_state.draft_items = [
        {"description": "Consulting Services", "quantity": 10.0, "unit_price": 150.00, "tax_rate": 0.10},
        {"description": "Infrastructure Setup", "quantity": 1.0, "unit_price": 500.00, "tax_rate": 0.10},
    ]
if "draft_customer" not in st.session_state:
    st.session_state.draft_customer = ""
if "draft_notes" not in st.session_state:
    st.session_state.draft_notes = "Payment due within 30 days."

# -----------------------------------------------------------------------------
# 1. Natural Language AI Draft Box
# -----------------------------------------------------------------------------
with st.expander("✨ Draft with AI (Natural Language Prompt)", expanded=True):
    st.caption("Describe the invoice naturally and let AI parse the line items and terms. LLM totals are discarded and recomputed in Python.")
    ai_prompt = st.text_area(
        "Describe invoice details:",
        placeholder="e.g. Bill Acme Corp $2,500 for 25 hours of full-stack engineering and $350 for cloud hosting, 10% tax, due in 30 days",
        height=75,
    )
    if st.button("🪄 Parse Draft with AI", type="secondary"):
        if ai_prompt.strip():
            with st.spinner("AI parsing invoice fields..."):
                try:
                    draft_res = client.draft_from_text(ai_prompt.strip())
                    parsed_lines = draft_res.get("line_items", [])
                    if parsed_lines:
                        st.session_state.draft_items = [
                            {
                                "description": pl.get("description", "Item"),
                                "quantity": float(pl.get("quantity", 1)),
                                "unit_price": float(pl.get("unit_price", 0.0)),
                                "tax_rate": float(pl.get("tax_rate", 0.0) or 0.0),
                            }
                            for pl in parsed_lines
                        ]
                    if draft_res.get("customer_name"):
                        st.session_state.draft_customer = draft_res.get("customer_name")
                    if draft_res.get("notes"):
                        st.session_state.draft_notes = draft_res.get("notes")
                    st.success("Draft parsed successfully! Line items and details updated below.")
                    st.rerun()
                except Exception as ex:
                    st.error(f"Failed to draft with AI: {ex}")
        else:
            st.warning("Please enter a description.")

st.markdown("---")

# -----------------------------------------------------------------------------
# 2. Customer & Invoice Parameters
# -----------------------------------------------------------------------------
# Fetch available customers
customers_list = []
try:
    c_res = client.get_customers()
    customers_list = c_res if isinstance(c_res, list) else c_res.get("items", [])
except Exception:
    customers_list = []

col_cust, col_curr, col_tax = st.columns([2, 1, 1])

with col_cust:
    if customers_list:
        customer_options = {c["name"]: c["id"] for c in customers_list}
        selected_cust_name = st.selectbox("Customer / Client:", list(customer_options.keys()))
        selected_customer_id = customer_options[selected_cust_name]
    else:
        st.warning("No customers found in database. Create one below:")
        new_c_name = st.text_input("New Customer Name", value=st.session_state.draft_customer or "Acme Corporation")
        new_c_email = st.text_input("New Customer Email", value="billing@example.com")
        if st.button("Save New Customer"):
            try:
                created_cust = client.create_customer({"name": new_c_name, "email": new_c_email})
                st.success(f"Customer '{new_c_name}' created!")
                st.rerun()
            except Exception as e:
                st.error(f"Error creating customer: {e}")
        st.stop()

with col_curr:
    currency = st.selectbox("Currency", ["USD", "EUR", "GBP", "CAD", "AUD"])

with col_tax:
    default_tax_rate = st.number_input("Default Tax Rate (e.g. 0.10 for 10%)", min_value=0.0, max_value=1.0, value=0.10, step=0.01)

col_d1, col_d2 = st.columns(2)
with col_d1:
    issue_date = st.date_input("Issue Date", value=date.today())
with col_d2:
    due_date = st.date_input("Due Date", value=date.today() + timedelta(days=30))

notes = st.text_area("Notes & Payment Instructions", value=st.session_state.draft_notes, height=68)

st.markdown("### 📦 Line Items")
st.caption("Add, edit, or remove line items. Quantities are handled with 4 decimal places, prices with commercial half-up rounding.")

df_editor = st.data_editor(
    pd.DataFrame(st.session_state.draft_items),
    num_rows="dynamic",
    use_container_width=True,
    column_config={
        "description": st.column_config.TextColumn("Description", required=True),
        "quantity": st.column_config.NumberColumn("Quantity", min_value=0.0001, step=1.0, format="%.4f"),
        "unit_price": st.column_config.NumberColumn("Unit Price", min_value=0.0, step=10.0, format="%.2f"),
        "tax_rate": st.column_config.NumberColumn("Tax Rate", min_value=0.0, max_value=1.0, step=0.01, format="%.2f"),
    },
    key="items_editor",
)

# -----------------------------------------------------------------------------
# 3. Live Read-Only Totals Panel (Strictly Computed in Python via Decimal)
# -----------------------------------------------------------------------------
st.markdown("### 🧮 Live Totals (Pure Decimal Arithmetic)")

calc_items = []
for _, row in df_editor.iterrows():
    desc = str(row.get("description", "")).strip()
    if not desc:
        continue
    try:
        qty = Decimal(str(row.get("quantity", 1)))
        price = Decimal(str(row.get("unit_price", 0)))
        tr = Decimal(str(row.get("tax_rate", default_tax_rate))) if row.get("tax_rate") is not None else Decimal(str(default_tax_rate))
        calc_items.append({"description": desc, "quantity": qty, "unit_price": price, "tax_rate": tr})
    except Exception:
        pass

calc_res = compute_totals(calc_items, default_tax_rate=Decimal(str(default_tax_rate)))

m1, m2, m3 = st.columns(3)
with m1:
    st.metric("Subtotal", format_currency(calc_res.subtotal, currency))
with m2:
    st.metric("Tax Amount", format_currency(calc_res.tax_amount, currency))
with m3:
    st.metric("Total Payable", format_currency(calc_res.total_amount, currency))

st.markdown("---")

# -----------------------------------------------------------------------------
# 4. Issue Outbound Invoice & Download PDF
# -----------------------------------------------------------------------------
col_issue, col_preview = st.columns([1, 2])

with col_issue:
    issue_btn = st.button("📤 Issue Invoice (Assign Gapless Number)", type="primary", use_container_width=True)

if issue_btn:
    if not calc_items:
        st.error("Cannot issue an empty invoice. Please add at least one line item.")
    else:
        with st.spinner("Assigning sequential number and creating invoice..."):
            try:
                line_items_payload = [
                    {
                        "description": item["description"],
                        "quantity": str(item["quantity"]),
                        "unit_price": str(item["unit_price"]),
                        "tax_rate": str(item["tax_rate"]),
                    }
                    for item in calc_items
                ]

                create_payload = {
                    "customer_id": selected_customer_id,
                    "issue_date": str(issue_date),
                    "due_date": str(due_date),
                    "currency": currency,
                    "default_tax_rate": str(default_tax_rate),
                    "notes": notes,
                    "line_items": line_items_payload,
                }

                issued_inv = client.create_issued_invoice(create_payload)
                inv_num = issued_inv.get("invoice_number")
                inv_id = issued_inv.get("id")

                st.success(f"🎉 Outbound Invoice **{inv_num}** successfully created and issued!")
                st.session_state["last_issued_id"] = inv_id
                st.session_state["last_issued_num"] = inv_num
            except Exception as e:
                st.error(f"Failed to issue invoice: {e}")

if "last_issued_id" in st.session_state:
    last_id = st.session_state["last_issued_id"]
    last_num = st.session_state.get("last_issued_num", "invoice")
    try:
        pdf_bytes = client.get_invoice_pdf(last_id)
        st.download_button(
            label=f"📥 Download A4 PDF ({last_num})",
            data=pdf_bytes,
            file_name=f"{last_num}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
    except Exception as ex:
        st.warning(f"Could not load PDF: {ex}")

