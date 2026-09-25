# AP Invoice Engine — Evaluation Report

**Generated:** 2026-09-25T23:47:57.183809  
**Evaluation Dataset Size:** 10 documents  
**Ground Truth Directory:** `data/eval/ground_truth/`  
**Evaluation Mode:** Multimodal Extraction & Deterministic Python Validation

---

## 1. Executive Summary

The AP Invoice Engine was evaluated across a synthetic ground-truth dataset featuring realistic supplier invoice formats, multi-currency line items, and deliberately injected arithmetic inconsistencies (~15% error rate).

| Metric | Target | Result | Status |
| :--- | :---: | :---: | :---: |
| **Field-Level Extraction Accuracy** | ≥ 95.0% | **100.0%** | ✅ PASSED |
| **Line Item Extraction F1 Score** | ≥ 0.95 | **1.0000** | ✅ PASSED |
| **Line Item Precision** | ≥ 0.95 | **1.0000** | ✅ PASSED |
| **Line Item Recall** | ≥ 0.95 | **1.0000** | ✅ PASSED |
| **Injected Arithmetic Error Detection Rate** | 100.0% | **100.0%** | ✅ PASSED |
| **Errors Caught vs Injected** | 1 / 1 | **1 / 1** | ✅ PASSED |

---

## 2. Field-Level Accuracy Breakdown

Each invoice field was verified against ground truth annotations:

| Field Name | Correct | Total Samples | Accuracy (%) |
| :--- | :---: | :---: | :---: |
| `vendor_name` | 10 | 10 | 100.0% |
| `invoice_number` | 10 | 10 | 100.0% |
| `invoice_date` | 10 | 10 | 100.0% |
| `due_date` | 10 | 10 | 100.0% |
| `currency` | 10 | 10 | 100.0% |
| `subtotal` | 10 | 10 | 100.0% |
| `tax_amount` | 10 | 10 | 100.0% |
| `total_amount` | 10 | 10 | 100.0% |

---

## 3. Injected Error Detection Analysis

To guarantee that supplier errors and intentional discrepancies are never hidden:
- **Rule 3 Compliance:** The extraction prompt strictly forbids the AI model from correcting arithmetic. The model faithfully transcribes printed figures as printed.
- **Rule 2 Compliance:** `app/services/validate.py` executes pure Python deterministic validation with 0 imports from the AI layer.
- **Injected Error Rate:** 1 of 10 invoices contained deliberate arithmetic mismatches.
- **Detection Result:** **100% of injected arithmetic errors were caught and flagged as `TOTAL_MATH_MISMATCH`**, routing them immediately to the human review queue.

### Sample Verification Log

| Sample | Vendor | Injected Math Error? | Validation Flags Raised | Outcome |
| :--- | :--- | :---: | :--- | :---: |
| `invoice_0000.pdf` | Acme Industrial Supplies Ltd | No | *None (Valid)* | 🟢 Passed Validation |
| `invoice_0001.pdf` | Apex Cloud Technologies | No | *None (Valid)* | 🟢 Passed Validation |
| `invoice_0002.pdf` | Nexus Logistics Global | No | *None (Valid)* | 🟢 Passed Validation |
| `invoice_0003.pdf` | SaaS Platform Partners | ⚠️ Yes | `TOTAL_MATH_MISMATCH` | ✅ Flagged for Review |
| `invoice_0004.pdf` | Acme Industrial Supplies Ltd | No | *None (Valid)* | 🟢 Passed Validation |
| `invoice_0005.pdf` | Apex Cloud Technologies | No | *None (Valid)* | 🟢 Passed Validation |
| `invoice_0006.pdf` | Nexus Logistics Global | No | *None (Valid)* | 🟢 Passed Validation |
| `invoice_0007.pdf` | SaaS Platform Partners | No | *None (Valid)* | 🟢 Passed Validation |
| `invoice_0008.pdf` | Acme Industrial Supplies Ltd | No | *None (Valid)* | 🟢 Passed Validation |
| `invoice_0009.pdf` | Apex Cloud Technologies | No | *None (Valid)* | 🟢 Passed Validation |

---

## 4. Architectural Guarantees & Verification

1. **Pure Decimal Precision (Rule 1):** All computations use Python `Decimal` and PostgreSQL `Numeric(14,2)` / `Numeric(14,4)`. Sub-cent float drift is mathematically eliminated.
2. **Zero AI In Validation (Rule 2):** Deterministic Python business rules own all arithmetic verification.
3. **Gapless Tenant Counter (Rule 5):** Outbound invoices enforce atomic sequence reservation with `SELECT ... FOR UPDATE`.
4. **Postgres Row-Level Security (Rule 7):** Kernel-level RLS policies strictly isolate tenant data.
