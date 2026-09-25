"""Financial Insights - Plotly dashboards for spend, unit price drift, and duplicate risks."""

import pandas as pd
import plotly.express as px
import streamlit as st

from ui.lib.api_client import APIClient
from ui.lib.formatters import format_currency

st.set_page_config(
    page_title="Financial Insights | AP Invoice Engine", page_icon="📊", layout="wide"
)

st.title("📊 Financial Insights & Analytics")
st.markdown(
    "Real-time accounts payable intelligence: spend concentration, historical trends, unit price drift, duplicate risks, and payment aging."
)

client = APIClient()

tab_spend, tab_drift, tab_duplicates, tab_aging = st.tabs(
    [
        "💰 Spend Analytics",
        "📈 Unit Price Drift",
        "⚠️ Duplicate Risks",
        "⏳ Payment Aging & Vendor Quality",
    ]
)

# -----------------------------------------------------------------------------
# TAB 1: Spend Analytics (Vendor Spend & Monthly Trend)
# -----------------------------------------------------------------------------
with tab_spend:
    st.subheader("Spend by Vendor & Currency")
    try:
        vendors_data = client.get_insights_vendors().get("vendors", [])
        if vendors_data:
            df_vendors = pd.DataFrame(vendors_data)
            # Numeric conversion
            df_vendors["total_spend"] = pd.to_numeric(df_vendors["total_spend"], errors="coerce")
            df_vendors["invoice_count"] = pd.to_numeric(
                df_vendors["invoice_count"], errors="coerce"
            )

            c_chart1, c_chart2 = st.columns([3, 2])
            with c_chart1:
                fig_spend = px.bar(
                    df_vendors,
                    x="vendor_name",
                    y="total_spend",
                    color="currency",
                    title="Total Spend by Supplier & Currency",
                    labels={
                        "vendor_name": "Supplier",
                        "total_spend": "Total Spend",
                        "currency": "Currency",
                    },
                    text_auto=".2s",
                )
                fig_spend.update_layout(xaxis_tickangle=-45, template="plotly_white")
                st.plotly_chart(fig_spend, use_container_width=True)

            with c_chart2:
                fig_pie = px.pie(
                    df_vendors,
                    names="vendor_name",
                    values="total_spend",
                    title="Supplier Spend Concentration",
                    hole=0.4,
                )
                fig_pie.update_layout(template="plotly_white")
                st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("No supplier invoice data available yet.")
    except Exception as e:
        st.error(f"Error loading vendor spend: {e}")

    st.markdown("---")
    st.subheader("Monthly Spend Trend")
    try:
        trend_data = client.get_insights_trend().get("trend", [])
        if trend_data:
            df_trend = pd.DataFrame(trend_data)
            df_trend["total_spend"] = pd.to_numeric(df_trend["total_spend"], errors="coerce")
            df_trend["month"] = df_trend["month"].astype(str)

            fig_trend = px.line(
                df_trend,
                x="month",
                y="total_spend",
                markers=True,
                title="Month-over-Month Accounts Payable Spend",
                labels={"month": "Month", "total_spend": "Spend Volume"},
            )
            fig_trend.update_layout(template="plotly_white")
            st.plotly_chart(fig_trend, use_container_width=True)
        else:
            st.info("No monthly trend data available yet.")
    except Exception as e:
        st.error(f"Error loading spend trend: {e}")

# -----------------------------------------------------------------------------
# TAB 2: Unit Price Drift
# -----------------------------------------------------------------------------
with tab_drift:
    st.subheader("Item Unit Price Drift (3+ Invoices)")
    st.caption(
        "Surfaces supplier price inflation or discrepancies where identical item descriptions were billed at varying unit prices over time."
    )

    try:
        drift_data = client.get_insights_price_drift(min_invoices=3).get("price_drift", [])
        if drift_data:
            df_drift = pd.DataFrame(drift_data)
            df_drift["min_unit_price"] = pd.to_numeric(df_drift["min_unit_price"], errors="coerce")
            df_drift["max_unit_price"] = pd.to_numeric(df_drift["max_unit_price"], errors="coerce")
            df_drift["avg_unit_price"] = pd.to_numeric(df_drift["avg_unit_price"], errors="coerce")
            df_drift["price_variance"] = pd.to_numeric(df_drift["price_variance"], errors="coerce")

            fig_drift = px.bar(
                df_drift,
                x="description",
                y=["min_unit_price", "avg_unit_price", "max_unit_price"],
                barmode="group",
                title="Unit Price Spread (Min vs Avg vs Max)",
                labels={
                    "value": "Unit Price",
                    "variable": "Metric",
                    "description": "Item Description",
                },
            )
            fig_drift.update_layout(xaxis_tickangle=-30, template="plotly_white")
            st.plotly_chart(fig_drift, use_container_width=True)

            st.dataframe(df_drift, use_container_width=True, hide_index=True)
        else:
            st.info("No items have met the 3+ invoice threshold for price drift analysis yet.")
    except Exception as e:
        st.error(f"Error loading price drift: {e}")

# -----------------------------------------------------------------------------
# TAB 3: Duplicate Risks
# -----------------------------------------------------------------------------
with tab_duplicates:
    st.subheader("Potential Duplicate Invoice Risks")
    st.caption(
        "Catch logical duplicates sharing identical (Vendor Name, Invoice Number) or identical SHA-256 file hashes."
    )

    try:
        dup_data = client.get_insights_duplicates().get("duplicates", [])
        if dup_data:
            st.warning(
                f"⚠️ Found {len(dup_data)} potential duplicate invoice clusters requiring attention!"
            )
            for cluster in dup_data:
                vendor = cluster.get("vendor_name", "Unknown")
                inv_num = cluster.get("invoice_number", "Unknown")
                count = cluster.get("duplicate_count", 2)
                tot = format_currency(cluster.get("total_amount"))

                with st.expander(
                    f"⚠️ {vendor} - Invoice #{inv_num} ({count} duplicate entries, {tot})",
                    expanded=True,
                ):
                    st.write(f"**Duplicate Count:** {count}")
                    st.write(f"**Invoice Number:** {inv_num}")
                    st.write(f"**Vendor:** {vendor}")
                    st.write(f"**Sample Total:** {tot}")
                    if st.button("Review Invoices in Directory", key=f"dup_{inv_num}"):
                        st.switch_page("pages/2_📚_Invoices.py")
        else:
            st.success("🟢 No logical duplicate invoices detected across your account.")
    except Exception as e:
        st.error(f"Error loading duplicate insights: {e}")

# -----------------------------------------------------------------------------
# TAB 4: Payment Aging & Vendor Quality
# -----------------------------------------------------------------------------
with tab_aging:
    col_ag, col_vq = st.columns(2)

    with col_ag:
        st.subheader("Payment Aging Distribution")
        try:
            aging_data = client.get_insights_aging().get("aging", [])
            if aging_data:
                df_aging = pd.DataFrame(aging_data)
                df_aging["total_amount"] = pd.to_numeric(df_aging["total_amount"], errors="coerce")

                fig_aging = px.bar(
                    df_aging,
                    x="aging_bucket",
                    y="total_amount",
                    color="aging_bucket",
                    title="Overdue AP Invoices by Aging Bucket",
                    labels={
                        "aging_bucket": "Aging Bracket",
                        "total_amount": "Total Overdue Amount",
                    },
                )
                fig_aging.update_layout(template="plotly_white")
                st.plotly_chart(fig_aging, use_container_width=True)
            else:
                st.info("No overdue invoice aging data available.")
        except Exception as e:
            st.error(f"Error loading aging insights: {e}")

    with col_vq:
        st.subheader("Supplier Quality & Error Rate")
        try:
            vq_data = client.get_insights_vendor_quality().get("vendor_quality", [])
            if vq_data:
                df_vq = pd.DataFrame(vq_data)
                df_vq["error_rate_pct"] = pd.to_numeric(df_vq["error_rate_pct"], errors="coerce")

                fig_vq = px.bar(
                    df_vq,
                    x="vendor_name",
                    y="error_rate_pct",
                    title="Supplier Invoice Validation Error Rate (%)",
                    labels={"vendor_name": "Supplier", "error_rate_pct": "Error Rate %"},
                    color="error_rate_pct",
                    color_continuous_scale="Reds",
                )
                fig_vq.update_layout(xaxis_tickangle=-30, template="plotly_white")
                st.plotly_chart(fig_vq, use_container_width=True)
            else:
                st.info("No vendor quality error rate data available.")
        except Exception as e:
            st.error(f"Error loading vendor quality: {e}")
