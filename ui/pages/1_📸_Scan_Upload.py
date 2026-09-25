"""Scan & Upload - Camera capture and file uploader with live polling and instant summary."""

import time

import streamlit as st

from ui.lib.api_client import APIClient
from ui.lib.formatters import format_currency, render_confidence_metric, render_status_pill

st.set_page_config(page_title="Scan & Upload | AP Invoice Engine", page_icon="📸", layout="wide")

st.title("📸 Scan & Upload Invoice")
st.markdown(
    "Capture physical invoices via webcam/mobile camera or upload existing PDF, PNG, JPG, or TIFF documents."
)

client = APIClient()

tab_camera, tab_file = st.tabs(["📷 Camera Capture", "📁 File Upload"])

uploaded_bytes = None
file_name = None
mime_type = "application/pdf"

with tab_camera:
    camera_photo = st.camera_input("Take a picture of the supplier invoice")
    if camera_photo is not None:
        uploaded_bytes = camera_photo.getvalue()
        file_name = f"camera_scan_{int(time.time())}.jpg"
        mime_type = "image/jpeg"
        st.image(uploaded_bytes, caption="Captured Image Preview", use_container_width=True)

with tab_file:
    uploaded_file = st.file_uploader(
        "Choose an invoice file",
        type=["pdf", "png", "jpg", "jpeg", "tiff"],
        help="Upload PDF documents or images up to 25MB",
    )
    if uploaded_file is not None:
        uploaded_bytes = uploaded_file.getvalue()
        file_name = uploaded_file.name
        mime_type = uploaded_file.type or "application/pdf"
        st.success(f"Selected file: **{file_name}** ({len(uploaded_bytes) / 1024:.1f} KB)")
        if mime_type.startswith("image/"):
            st.image(uploaded_bytes, caption="Image Preview", use_container_width=True)

if uploaded_bytes and file_name:
    st.markdown("---")
    col_btn, col_info = st.columns([1, 3])
    with col_btn:
        start_processing = st.button("🚀 Process Invoice", type="primary", use_container_width=True)
    with col_info:
        st.caption(f"Ready to ingest: **{file_name}** • Tenant: `{client.tenant_id}`")

    if start_processing:
        with st.status("Ingesting document and extracting data...", expanded=True) as status_box:
            st.write("📤 Uploading document to API backend...")
            try:
                res = client.upload_document(
                    uploaded_bytes, filename=file_name, mime_type=mime_type
                )
            except Exception as e:
                status_box.update(label="Upload failed", state="error", expanded=True)
                st.error(f"Failed to upload document: {e}")
                st.stop()

            doc_id = res.get("document_id")
            is_dup = res.get("is_duplicate", False)

            if is_dup:
                status_box.update(label="Duplicate file detected", state="complete")
                st.warning(
                    "⚠️ This document was previously uploaded (SHA-256 match). Showing existing record."
                )
            else:
                st.write(
                    "🔍 Queued in background worker pipeline. Extracting fields and validating arithmetic..."
                )
                # Poll for completion
                max_polls = 20
                completed = False
                for i in range(max_polls):
                    time.sleep(1.5)
                    doc_status = "PENDING"
                    try:
                        doc_info = client.get_document(doc_id)
                        doc_status = doc_info.get("status", "PENDING")
                    except Exception:
                        pass

                    if doc_status in ("COMPLETED", "NEEDS_REVIEW", "completed", "needs_review"):
                        completed = True
                        break
                    elif doc_status in ("FAILED", "failed"):
                        status_box.update(label="Processing failed in pipeline", state="error")
                        st.error("Extraction pipeline failed on this document.")
                        st.stop()

                status_box.update(label="Extraction & validation complete!", state="complete")

        # Instant Summary Card
        st.markdown("## 📋 Instant Summary Card")
        latest = client.get_latest_document()
        if latest and latest.get("invoice"):
            inv = latest["invoice"]
            items = latest.get("line_items", [])
            flags = latest.get("validation_flags", [])

            card_col1, card_col2, card_col3 = st.columns([2, 1, 1])
            with card_col1:
                st.subheader(f"{inv.get('vendor_name') or 'Unknown Vendor'}")
                st.caption(
                    f"Invoice #: **{inv.get('invoice_number') or 'N/A'}** | File: {file_name}"
                )
            with card_col2:
                st.write("**Status:**")
                render_status_pill(inv.get("status", "completed"))
            with card_col3:
                st.write("**Extraction Confidence:**")
                render_confidence_metric(inv.get("confidence_score"))

            st.markdown("---")
            m1, m2, m3, m4 = st.columns(4)
            with m1:
                st.metric(
                    "Total Amount",
                    format_currency(inv.get("total_amount"), inv.get("currency", "USD")),
                )
            with m2:
                st.metric(
                    "Subtotal", format_currency(inv.get("subtotal"), inv.get("currency", "USD"))
                )
            with m3:
                st.metric(
                    "Tax Amount", format_currency(inv.get("tax_amount"), inv.get("currency", "USD"))
                )
            with m4:
                st.metric("Line Items Count", len(items))

            if flags:
                st.markdown("#### ⚠️ Validation Warnings & Flags")
                for f in flags:
                    sev = f.get("severity", "WARNING").upper()
                    if sev in ("ERROR", "CRITICAL"):
                        st.error(f"**[{sev}] {f.get('flag_type')}:** {f.get('message')}")
                    else:
                        st.warning(f"**[{sev}] {f.get('flag_type')}:** {f.get('message')}")

            st.markdown("---")
            act1, act2, act3 = st.columns(3)
            with act1:
                if inv.get("status") == "needs_review" or flags:
                    if st.button(
                        "🔎 Review in Human Queue", type="primary", use_container_width=True
                    ):
                        st.switch_page("pages/5_✅_Review_Queue.py")
            with act2:
                if st.button("📄 View Full Result", use_container_width=True):
                    st.switch_page("pages/3_📄_Last_Result.py")
            with act3:
                if st.button("📚 View All Invoices", use_container_width=True):
                    st.switch_page("pages/2_📚_Invoices.py")
        else:
            st.info(
                "Document uploaded successfully. Processing will update record in Invoices Directory."
            )
            if st.button("Go to Invoices Directory"):
                st.switch_page("pages/2_📚_Invoices.py")
