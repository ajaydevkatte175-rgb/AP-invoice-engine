"""AP Invoice Engine - Home & Dashboard Overview."""

import streamlit as st

from ui.lib.api_client import APIClient
from ui.lib.formatters import format_currency

st.set_page_config(
    page_title="AP Invoice Engine",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="expanded",
)

client = APIClient()

# Header
col_header, col_status = st.columns([3, 1])
with col_header:
    st.title("🧾 AP Invoice Engine")
    st.caption(
        "Autonomous Accounts Payable Extraction, Deterministic Validation & Outbound Invoicing"
    )

with col_status:
    health = client.check_health()
    if health.get("status") == "ok":
        st.success("🟢 API & Database Online")
    else:
        st.warning("🟡 System Offline / Degraded")

st.markdown("---")

# Fetch headline metrics
summary = {}
try:
    summary = client.get_insights_summary()
except Exception:
    pass

ap_stats = summary.get("ap", {})
ar_stats = summary.get("ar", {})

# Metric Row
m1, m2, m3, m4, m5 = st.columns(5)
with m1:
    st.metric(
        label="Total AP Spend",
        value=format_currency(ap_stats.get("total_spend", 0)),
        help="Total volume of supplier invoices received",
    )
with m2:
    st.metric(
        label="Total AR Issued",
        value=format_currency(ar_stats.get("total_issued_amount", 0)),
        help="Total receivables billed to customers",
    )
with m3:
    review_count = ap_stats.get("review_queue_count", 0)
    st.metric(
        label="Review Queue",
        value=f"{review_count} Invoices",
        delta=f"{review_count} pending" if review_count > 0 else "All clear",
        delta_color="inverse" if review_count > 0 else "normal",
        help="Invoices routed to human review due to arithmetic mismatches or confidence thresholds",
    )
with m4:
    st.metric(
        label="Invoices Processed",
        value=ap_stats.get("total_invoices", 0),
        help="Total inbound documents processed",
    )
with m5:
    avg_conf = ap_stats.get("avg_extraction_confidence", 0.0)
    st.metric(
        label="Avg Extraction Accuracy",
        value=f"{float(avg_conf) * 100:.1f}%",
        help="Mean field-level extraction confidence score",
    )

st.markdown("### 🚀 Quick Navigation")

# Navigation Card Grid
c1, c2, c3 = st.columns(3)

with c1:
    st.markdown(
        """
        <div style="border: 1px solid #e2e8f0; border-radius: 12px; padding: 20px; margin-bottom: 20px; background: #ffffff;">
            <h3 style="margin-top:0;">📸 Scan & Upload</h3>
            <p style="color: #64748b;">Capture invoices via phone camera or upload PDFs/images for automated multimodal extraction.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if st.button("Go to Scan & Upload", key="btn_nav_upload", use_container_width=True):
        st.switch_page("pages/1_📸_Scan_Upload.py")

    st.markdown(
        """
        <div style="border: 1px solid #e2e8f0; border-radius: 12px; padding: 20px; margin-bottom: 20px; background: #ffffff;">
            <h3 style="margin-top:0;">📊 Financial Insights</h3>
            <p style="color: #64748b;">Analyze vendor spend distribution, unit price drift over time, and duplicate payment risks.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if st.button("Go to Financial Insights", key="btn_nav_insights", use_container_width=True):
        st.switch_page("pages/4_📊_Insights.py")

with c2:
    st.markdown(
        """
        <div style="border: 1px solid #e2e8f0; border-radius: 12px; padding: 20px; margin-bottom: 20px; background: #ffffff;">
            <h3 style="margin-top:0;">✅ Review Queue</h3>
            <p style="color: #64748b;">Inspect flagged invoices side-by-side with original document scans and live arithmetic validation.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if st.button("Open Review Queue", key="btn_nav_review", use_container_width=True):
        st.switch_page("pages/5_✅_Review_Queue.py")

    st.markdown(
        """
        <div style="border: 1px solid #e2e8f0; border-radius: 12px; padding: 20px; margin-bottom: 20px; background: #ffffff;">
            <h3 style="margin-top:0;">🧾 Create Invoice</h3>
            <p style="color: #64748b;">Draft outbound invoices using natural language with guaranteed Python arithmetic and A4 PDF generation.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if st.button("Create Outbound Invoice", key="btn_nav_create", use_container_width=True):
        st.switch_page("pages/6_🧾_Create_Invoice.py")

with c3:
    st.markdown(
        """
        <div style="border: 1px solid #e2e8f0; border-radius: 12px; padding: 20px; margin-bottom: 20px; background: #ffffff;">
            <h3 style="margin-top:0;">📚 Invoices Directory</h3>
            <p style="color: #64748b;">Search, filter, and inspect full historical records for inbound AP and outbound AR invoices.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if st.button("View Invoices Directory", key="btn_nav_invoices", use_container_width=True):
        st.switch_page("pages/2_📚_Invoices.py")

    st.markdown(
        """
        <div style="border: 1px solid #e2e8f0; border-radius: 12px; padding: 20px; margin-bottom: 20px; background: #ffffff;">
            <h3 style="margin-top:0;">🤖 Ask AI Assistant</h3>
            <p style="color: #64748b;">Ask questions in natural language and inspect verified SQL queries generated and executed against the database.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if st.button("Ask AI Assistant", key="btn_nav_ask", use_container_width=True):
        st.switch_page("pages/7_🤖_Ask_AI.py")

st.markdown("---")
st.caption("AP Invoice Engine v0.1.0 • Built with FastAPI, PostgreSQL RLS, ARQ, and Streamlit")
