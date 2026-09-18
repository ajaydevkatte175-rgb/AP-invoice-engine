# AGENTS.md — AP Invoice Engine Specification & Execution Rules

You are building a production-quality invoice platform with me. Work in PHASES.
At the end of each phase, RUN the verification commands yourself, show me the output, and STOP. Do not begin the next phase until I say "continue".

---

## WHAT WE ARE BUILDING AND WHY

### The business problem
Accounts payable staff manually retype line-item data from supplier invoices — digital PDFs, scans, and phone photographs — into accounting software. It is high-volume, low-judgement work. Errors surface late as duplicate payments, payment disputes, and month-end reconciliation gaps. Generic OCR fails because vendor layouts vary without limit. Template extraction fails because every new vendor needs configuration.

Separately, small businesses and freelancers create outgoing invoices manually in word processors or spreadsheets, producing inconsistent documents with arithmetic errors and non-sequential numbering.

### The solution
One platform that does both:

**INBOUND (accounts payable)**
1. Accept a photograph taken on a phone, or a browsed PDF/PNG/JPG/TIFF
2. Extract structured invoice data — header fields AND line items — against a strict Pydantic schema using a vision LLM with a bounded repair loop
3. Validate arithmetic DETERMINISTICALLY in plain Python
4. Show an instant summary the moment processing finishes
5. Route anything uncertain or arithmetically inconsistent to a human review queue showing the source image beside an editable form
6. Remember everything — full searchable history, server-side

**OUTBOUND (accounts receivable)**
7. Create and issue invoices from a form with dynamic line items, live calculated totals, gapless numbering, and PDF download
8. Let the user describe an invoice in natural language and have the form pre-filled — but ALWAYS recompute totals in Python and ALWAYS require human confirmation before issuing

**INSIGHT AND ASSISTANCE**
9. A dashboard surfacing real business patterns: spend by vendor and currency, month-over-month trend, vendor concentration, unit price drift, duplicate risk, payment aging, validation error rate by vendor, tax anomalies
10. A grounded AI agent answering questions about the data via TEXT-TO-SQL, showing the generated query with every answer
11. An append-only audit log of every AI decision and every human correction

---

## NON-NEGOTIABLE RULES

Breaking any of these means the work is wrong, even if it runs.

1. **Money is `Decimal` in Python and `Numeric(14,2)` in Postgres. NEVER `float`.** Float arithmetic causes sub-cent drift that makes the validation engine flag correct invoices. Quantities are `Numeric(14,4)`.
2. **`app/services/validate.py` must NOT import anything from the AI layer.** All arithmetic, date, and currency checks are deterministic Python.
3. **The extraction prompt must FORBID the model from correcting arithmetic.** It reports the printed subtotal exactly as shown, even when wrong. A model that "helpfully" fixes a mismatch hides the supplier error we exist to catch.
4. **Invoice generation totals are computed in Python, never by the LLM.** If the model proposes a total, discard it and recompute. The AI fills fields; Python does arithmetic; a human confirms before issuing.
5. **Invoice numbers must be gapless and unique per tenant.** Use a counter row with `SELECT ... FOR UPDATE`. Never `COUNT(*) + 1`.
6. **The AI agent generates SQL that is validated before execution** by a guard using `sqlglot`: exactly one statement, SELECT only, table allowlist, `tenant_id` filter injected if absent, LIMIT capped. It executes as a READ-ONLY Postgres role with a 5-second `statement_timeout`.
7. **Database Tenant Safety Net (Postgres RLS):** In addition to `sqlglot`, Postgres Row-Level Security (RLS) MUST be enabled on all tenant tables. The `invoice_ro` role MUST set `SET LOCAL app.current_tenant = 'tenant-uuid'` on every query connection so cross-tenant leakage is impossible at the database kernel level.
8. **The agent makes TWO model calls** — one to generate SQL, one to explain the rows actually returned. Never one call that generates SQL and predicts the answer; that lets it state a number it never computed.
9. **Never auto-execute an irreversible action.** No payments, no sending, no writes to external systems without explicit human action.
10. **Bounded repair loop**: maximum 2 repair attempts, then route to human review. Never unbounded.
11. **Page Processing Limit (Budget Guard):** If a PDF exceeds 5 pages, preprocess and extract ONLY the first 3 pages and the last 2 pages (where totals and line items reside) to prevent token budget blowouts on long T&C attachments.
12. **Logical Duplicate Check:** SHA256 catches duplicate files. Post-extraction, you MUST check for logical duplicates via `(vendor_name, invoice_number)`. Flag logical duplicates as `DUPLICATE_WARNING` and route to review.
13. **Every test must pass with NO API key set.** Use recorded fixtures. A test that needs a live key is not a test, it is a bill.
14. **Secrets live only in `.env`, which is gitignored.** Never in code, never in a committed file, never in chat output.
15. **Separate tables for inbound and outbound.** Received invoices go in `invoices`; issued invoices go in `issued_invoices`. Never mix them.

---

## TECH STACK — decided, do not substitute

- Python 3.12, `uv` for dependencies
- FastAPI (async) + Pydantic v2
- PostgreSQL 16 + SQLAlchemy 2.0 async + Alembic
- Redis + ARQ for background jobs
- Anthropic Claude — vision extraction, agent SQL, draft invoice parsing
- pypdf, pdfplumber, pdf2image, Pillow, opencv-python-headless
- reportlab for PDF generation
- sqlglot for SQL validation
- Streamlit + Plotly for the UI
- structlog for logging, pytest for tests
- Docker Compose for local infrastructure

Do NOT introduce LangChain, LangGraph, or any agent framework. The pipeline is linear: preprocess → extract → validate → route.

---

## PHASE 1 — Scaffold and infrastructure

Create:
- Full directory tree: `app/{core,api,services,services/agent,schemas,models,workers}`, `ailayer/{providers}`, `ui/{pages,lib,.streamlit}`, `prompts/`, `migrations/`, `data/{uploads,eval,seed}`, `scripts/`, `tests/{unit,integration}`, `docs/`
- `pyproject.toml` with the stack above
- `docker-compose.yml` — postgres:16-alpine and redis:7-alpine with healthchecks
- `.env.example` with every variable documented and no values
- `.gitignore` including `.env` and `data/uploads/*`
- `Makefile`: setup, up, down, migrate, seed, run, worker, ui, test, eval, lint
- `.vscode/settings.json`, `launch.json` (with a compound "Run everything" launching API + worker + UI), `tasks.json`
- `app/core/config.py` (Pydantic Settings), `db.py` (async engine with `pool_pre_ping=True`), `logging.py` (structlog)
- `app/main.py` with `GET /health` checking the database

**VERIFY (run these and show me the output):**
```bash
docker compose up -d && sleep 5 && docker compose ps
uv sync
uv run uvicorn app.main:app --port 8000 &
sleep 3 && curl -s localhost:8000/health

## PHASE 2 — Complete data model & Database RLS

SQLAlchemy models with `UUIDMixin`, `TimestampMixin`, `TenantMixin`:

**Inbound:** `documents`, `invoices`, `line_items`, `validation_flags`, `review_items`
**Outbound:** `tenant_profile`, `invoice_counters`, `customers`, `issued_invoices`, `issued_line_items`
**System:** `llm_calls`, `audit_log`, `chat_sessions`, `chat_messages`

* All money columns `Numeric(14,2)`. All quantity columns `Numeric(14,4)`.
* Unique constraint on `(tenant_id, invoice_number)` for `issued_invoices`.
* Add a `tax_breakdown` JSONB column to `invoices` to store multi-tax line breakdowns.
* Enable Postgres Row-Level Security (RLS) policies in the Alembic migration on all tenant-facing tables: `CREATE POLICY tenant_isolation ON invoices USING (tenant_id = current_setting('app.current_tenant', true)::uuid);`.

Alembic setup with `env.py` reading `DATABASE_URL` from settings. Generate the initial migration.

**VERIFY:**

```bash
uv run alembic upgrade head
docker compose exec -T postgres psql -U postgres -d invoicedb -c "\dt"
docker compose exec -T postgres psql -U postgres -d invoicedb -c "\d invoices"
uv run alembic downgrade base && uv run alembic upgrade head

```

Confirm 14 tables and that money columns show `numeric(14,2)`. STOP.


## PHASE 3 — Core API Endpoints, CRUD Operations, & Tenant Middleware

### Objectives:
1. **Tenant Middleware & Context (`app/core/middleware.py`)**:
   - Implement FastAPI middleware or dependency injection to extract/set the current tenant (`app.current_tenant`) for PostgreSQL Row-Level Security on every request.

2. **Core CRUD Services (`app/services/`)**:
   - Implement asynchronous business logic services handling creation, reading, updating, and listing for Inbound (AP) and Outbound (AR) entities (Vendors, Invoices, Line Items, Customers).

3. **FastAPI Routers (`app/api/v1/`)**:
   - Create clean, modular APIRouters:
     - `/vendors`: Vendor management endpoints.
     - `/invoices`: Inbound invoice upload, status updates, and line item retrieval.
     - `/customers` & `/issued-invoices`: Outbound AR endpoints.
   - Ensure all endpoints enforce strict Pydantic validation schemas and proper status codes.

### VERIFY:
- Run unit tests and verify API routes load correctly in the Swagger UI (`http://127.0.0.1:8000/docs`).

## PHASE 4 — The AI layer

### Objectives:
Build the reusable `ailayer/` package containing:
1. **Core Configuration & Types**:
   - `types.py`, `errors.py`, and `config.py` (handling `ai.yaml` configuration).
2. **Anthropic Provider Adapter (`ailayer/providers/anthropic.py`)**:
   - Set `max_retries=0` on the SDK client (the router owns retries).
   - Track token usage and compute execution cost.
3. **Robust Infrastructure**:
   - `router.py`: Failover chain with jittered exponential backoff.
   - `budget.py`: Hard monthly ceiling check executed **before** each call.
   - `prompts.py`: Loader for versioned markdown prompts featuring YAML frontmatter.
   - `structured.py`: Schema enforcement featuring a bounded repair loop (max 2 attempts).
   - `ledger.py`: Persistence layer writing one `llm_calls` row per call (including failed attempts).

### Prompt Files:
- `prompts/extract_invoice/v1.md`
- `prompts/repair/v1.md`
- **Constraint**: System prompt MUST explicitly state: *"Do NOT perform arithmetic. Report printed numbers exactly as shown."*

### VERIFY:
- Run unit tests without requiring a live API key:
  ```bash
  uv run pytest tests/unit/test_router.py tests/unit/test_structured.py -v
  

## PHASE 5 — API, worker, pipeline, & logical duplicates

`app/api/deps.py` — API key auth using `secrets.compare_digest`
`app/api/documents.py` — `POST /documents` (202, SHA256 duplicate detection)
`app/workers/tasks.py` — pipeline: preprocess → extract → merge → confidence → validate → logical duplicate check `(vendor_name, invoice_number)` → persist → route to review or complete → audit log. Thread correlation ID through everything.

**VERIFY:**

```bash
uv run python scripts/generate_invoices.py --count 3
make run & make worker &
KEY=$(grep ^API_KEY .env | cut -d= -f2)
curl -s -X POST localhost:8000/documents -H "X-API-Key: $KEY" -F "file=@data/eval/pdfs/invoice_0000.pdf"
sleep 20
curl -s localhost:8000/documents/latest -H "X-API-Key: $KEY" | python3 -m json.tool

```

Show extracted invoice with line items and flags. STOP.

---


