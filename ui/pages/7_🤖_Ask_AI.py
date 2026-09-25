"""Ask AI - Natural language financial assistant with sqlglot validation and Postgres RLS query inspection."""

import pandas as pd
import streamlit as st

from ui.lib.api_client import APIClient

st.set_page_config(page_title="Ask AI Assistant | AP Invoice Engine", page_icon="🤖", layout="wide")

st.title("🤖 Ask AI Financial Assistant")
st.markdown(
    "Query your accounts payable and receivable data using natural language. "
    "Every answer is grounded in database execution via **sqlglot guard validation**, "
    "**Postgres Row-Level Security (RLS)**, and two-step verification (Rule 6, 7 & 8)."
)

client = APIClient()

# Initialize chat session history
if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = [
        {
            "role": "assistant",
            "content": "Hello! I can answer questions about your supplier invoices, spend totals, unit price drift, and review queue. Ask me anything!",
            "sql": None,
            "rows": None,
        }
    ]

# Preset quick questions
st.markdown("##### 💡 Suggested Questions")
q_cols = st.columns(4)
suggested_q = None

with q_cols[0]:
    if st.button("📊 Total spend by vendor & currency?", use_container_width=True):
        suggested_q = "What is total spend by vendor and currency?"
with q_cols[1]:
    if st.button("⚠️ Invoices requiring review?", use_container_width=True):
        suggested_q = "Which invoices currently need human review and why?"
with q_cols[2]:
    if st.button("📈 Any unit price increases?", use_container_width=True):
        suggested_q = "Show me items with unit price drift across multiple invoices."
with q_cols[3]:
    if st.button("🎯 Average extraction accuracy?", use_container_width=True):
        suggested_q = (
            "What is our average extraction confidence score across all processed documents?"
        )

# Display conversation messages
for msg in st.session_state.chat_messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("sql"):
            with st.expander(
                "🔍 SQL Query Inspection (Validated by sqlglot & RLS)", expanded=False
            ):
                st.code(msg["sql"], language="sql")
                rows = msg.get("rows")
                if rows:
                    st.caption(f"Returned {len(rows)} row(s):")
                    st.dataframe(pd.DataFrame(rows), use_container_width=True)
                else:
                    st.caption("Query executed successfully (0 rows returned).")

# Chat input
user_input = st.chat_input("Ask a question about your invoices or financial data...")

prompt_to_send = suggested_q or user_input

if prompt_to_send:
    # Append user question
    st.session_state.chat_messages.append({"role": "user", "content": prompt_to_send})
    with st.chat_message("user"):
        st.markdown(prompt_to_send)

    # Call AI agent
    with st.chat_message("assistant"):
        with st.spinner(
            "Analyzing question, generating guarded SQL, and executing under Postgres RLS..."
        ):
            try:
                res = client.ask_ai(prompt_to_send)
                answer = res.get("answer") or "Here is the result of your query."
                sql = res.get("sql_query")
                rows = res.get("rows", [])

                st.markdown(answer)

                if sql:
                    with st.expander(
                        "🔍 SQL Query Inspection (Validated by sqlglot & RLS)", expanded=True
                    ):
                        st.code(sql, language="sql")
                        if rows:
                            st.caption(f"Returned {len(rows)} row(s):")
                            st.dataframe(pd.DataFrame(rows), use_container_width=True)
                        else:
                            st.caption("Query executed successfully (0 rows returned).")

                st.session_state.chat_messages.append(
                    {"role": "assistant", "content": answer, "sql": sql, "rows": rows}
                )
            except Exception as e:
                err_msg = f"Sorry, I encountered an error answering your question: {e}"
                st.error(err_msg)
                st.session_state.chat_messages.append(
                    {"role": "assistant", "content": err_msg, "sql": None, "rows": None}
                )
