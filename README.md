# Python QA Automation — Financial Services Sales Data Pipeline

**Submission 2 — Test Design and Automation Build**

A modular, configuration-driven Python validation suite that verifies data accuracy across every
layer of the sales data pipeline: **Source → Staging → Data Mart → Aggregates → Reporting**.

All table and column names are **verified against the project DDL scripts**
(`01Cretaedatabase` – `04CreateReportingViews`). Every automated check is traceable to a QA risk
identified in Submission 1.

> **Verified runnable.** `tests/run_against_mock.py` executes every validator against a seeded
> SQLite replica of the schema: **0 runtime errors, 0 duplicate IDs, 17 of 17 seeded defects
> detected, 38 unit tests passing.**

---

## 1. Quick start

```bash
cd qa_automation
python -m venv venv
venv\Scripts\activate            # Windows
# source venv/bin/activate       # Linux / macOS
pip install -r requirements.txt

# 1. Prove the code runs (no lab access needed)
python tests/mock/build_mock_db.py
python tests/run_against_mock.py
python -m pytest tests/ -q

# 2. Configure for the lab
copy .env.sample .env            # then edit .env

# 3. Verify connectivity
python -m src.main --health-check

# 4. Execute
python -m src.main --date 2026-05-01 --regression

# 5. Generate the Final QA Summary
python -m src.utils.qa_summary --auto
```

---

## 2. Verified schema

| Database | Objects |
|---|---|
| `fs_source_retail` | `retail_sales_raw`, `customer_master_raw`, `product_master_raw`, `branch_region_mapping_raw` |
| `fs_source_distributor` | `distributor_sales_raw` |
| `fs_source_online` | `online_sales_raw` |
| `fs_staging` | `stg_retail_sales`, `stg_distributor_sales`, `stg_online_sales`, `stg_customer_master`, `stg_product_master`, `stg_branch_region_mapping` |
| `fs_datamart` | `dm_sales_transaction`, `dm_sales_daily_summary`, `dm_sales_channel_summary`, `dm_sales_region_summary`, `dm_sales_product_summary`, `dm_executive_sales_summary` |
| `fs_reporting` | `vw_executive_dashboard`, `vw_channel_performance`, `vw_region_performance`, `vw_product_performance`, `vw_daily_sales_trend` |

**Schema facts that shaped the test design**

- Master/reference tables live in **`fs_source_retail`** — there is no separate master database.
- The status column is **`transaction_status`** in every source and staging table.
- `retail_sales_raw` and `online_sales_raw` already use `transaction_id`; only
  **`distributor_txn_id` → `transaction_id`** is renamed on load.
- Staging tables **drop the primary key**, so duplicate-key checks are genuinely required.
- There is **no `reversal_amount`** column → `net_sales_amount = gross_sales_amount − discount_amount`.
- There is **no `zone_name`** column; `region_name` is the lowest geographic grain.
- There is **no `distributor_master`** table; distributor region resolves via `region_code`.
- **`active_flag` is a TINYINT** (1 = active, 0 = inactive), not a Y/N flag.
- `dm_sales_transaction` uses the surrogate key **`sales_transaction_id`** (VARCHAR(70)) and renames
  `gross_amount` to **`gross_sales_amount`**.
- `dm_sales_transaction` does **not retain `product_type_raw`**, so raw-label checks join back to
  staging via `product_code`.
- `dm_executive_sales_summary` uses **`total_transactions`** and **`total_net_sales_amount`**.
- `dm_sales_product_summary` is grained at **`product_code`**.
- All five reporting views are `SELECT *` of their data mart table, so **view vs data mart must
  reconcile exactly**.

---

## 3. Coverage

**86 test cases** covering **54 risks**, with full bidirectional traceability.
83 are automated; the sheet and the code are an **exact match** with no drift.

| Layer | Cases | Coverage |
|---|---|---|
| **Source-to-Staging** | 32 | Counts, status filters, duplicates, key/rename integrity, nulls, schema, data types, field-level comparison, business date, load lineage, API health/auth/pagination/JSON schema/date range |
| **Data Quality** | 10 | Mobile and state formats, KYC compliance, DOB plausibility, domain values, orphan product/customer/branch references, conditional mandatory fields |
| **Staging-to-Data Mart** | 16 | Product harmonisation (incl. ULIP), name/category standardisation, channel mapping, region derivation for all three channels, customer cleansing, state enrichment, net-amount recomputation, inactive products, count reconciliation, surrogate key uniqueness |
| **Aggregate Models** | 13 | Region / channel / product / daily summaries, five individually-traceable executive metrics, average ticket size, top region and channel, cross-summary consistency |
| **Data Mart-to-Reporting** | 12 | All five views vs data mart, all five dashboard pages, filters, freshness, cross-report reconciliation |
| **Regression** | 3 | Post-fix, per-load, and seeded-defect detection runs |

**Execution order.** Validations are ordered so upstream causes are triaged first:
`0` connectivity → `1` source-to-staging → `2` data mart → `3` aggregates → `4` reporting →
`5` regression. A data mart failure is usually a symptom of a staging failure, so always resolve
the lowest-numbered layer first.

---

## 4. Project structure

```
qa_automation/
├── config/
│   ├── db_config.yaml           # 6 MySQL schemas (env placeholders)
│   ├── api_config.yaml          # API settings incl. max_pages guard
│   ├── source_config.yaml       # Source objects, filters, mandatory & compare columns
│   ├── validation_rules.yaml    # Column contracts, harmonisation matrix, severity map,
│   │                            # business-impact statements, patterns, tolerances
│   └── reporting_config.yaml    # 5 views, dashboards and measures
├── src/
│   ├── main.py                  # CLI entry point / orchestrator
│   ├── connectors/              # MySQL, CSV, REST API, reporting pages
│   ├── validators/              # base + 4 layer validators
│   └── utils/
│       ├── config_loader.py     # YAML + ${ENV_VAR}; reports ALL missing vars at once
│       ├── logger.py            # per-run log file
│       ├── result.py            # ValidationResult (variance, BLOCKED status)
│       ├── report_generator.py  # Execution summary / layer-wise / detailed results
│       ├── defect_logger.py     # Defect log with root cause and business impact
│       └── qa_summary.py        # Final QA Summary generator
├── tests/
│   ├── test_base_validator.py   # 38 unit tests (no DB/API needed)
│   ├── mock/build_mock_db.py    # seeded SQLite replica of the pipeline
│   └── run_against_mock.py      # executes every validator against the replica
├── reports/                     # Generated reports, defect logs and evidence
├── logs/                        # Per-run execution logs
├── requirements.txt
├── .env.sample
└── README.md
```

---

## 5. Configuration

**No credentials are stored in code or config.** All sensitive values resolve from environment
variables at runtime. If any are unset, the suite raises a single error listing **all** of them.

```
DB_HOST=127.0.0.1
DB_USER=learner_user
DB_PASSWORD=your-password
API_BASE_URL=http://127.0.0.1:5001/api
API_KEY=your-api-key
REPORT_BASE_URL=http://127.0.0.1:5002
RETAIL_CSV_PATH=C:/QA-Automation/tf-financial-sales-env/source_files/retail_sales_raw.csv
QA_ENVIRONMENT=LAB-GCP-MySQL
```

> `.env` must never be committed. It is listed in `.gitignore`.

---

## 6. Running the suite

```bash
python -m src.main --health-check                      # connectivity only
python -m src.main --date 2026-05-01                   # full run
python -m src.main --date 2026-05-01 --layer datamart  # single layer
python -m src.main --date 2026-05-01 --regression      # every layer
python -m src.main --date 2026-05-01 --use-csv         # retail from CSV
```

**Exit codes:** `0` all passed · `1` failures or blocked checks · `2` configuration error.

---

## 7. Outputs

| Output | Location |
|---|---|
| Validation report (Execution Summary, Layer-wise, Detailed, Failures, Blocked) | `reports/validation_report_<timestamp>.xlsx` |
| HTML report | `reports/validation_report_<timestamp>.html` |
| Defect log (12 required fields incl. root cause and business impact) | `reports/defect_log_<timestamp>.xlsx` |
| Evidence files (one JSON per failure or blocked check) | `reports/evidence/` |
| Execution log | `logs/execution_<timestamp>.log` |
| Final QA Summary | `reports/Final_QA_Summary_<timestamp>.xlsx` |

Every detailed result records Validation ID, layer, description, risk reference, source object,
target object, expected, actual, **variance**, status, severity and remarks.

**Status vocabulary.** `PASS` · `FAIL` (a genuine data defect) · `SKIPPED` (not applicable — no
rows) · `BLOCKED` (could not execute — environment, access or missing object). Only `FAIL`
results become defects, so the defect log never contains infrastructure noise.

---

## 8. Mentor review — findings and optimizations

| # | Area | Issue found | Change applied |
|---|------|-------------|----------------|
| 1 | **Duplicate validation IDs** | `AGG-V06` was reused for all five executive metrics. | Split into `AGG-V06a`–`AGG-V06e`. |
| 2 | **False failure in DM-V10** | The salutation regex ran *before* trimming, so a leading space defeated the `^` anchor and raised a defect that did not exist. | Trim first, then strip the salutation case-insensitively. Salutation list is configurable. |
| 3 | **Full table scans** | Orphan checks read entire staging tables with no date filter. | `SELECT DISTINCT` with a `sale_date` filter and `NOT NULL` predicate. |
| 4 | **Unbounded pagination** | `fetch_all_pages()` could loop indefinitely on a broken API. | Configurable `max_pages` guard (default 200). |
| 5 | **Blocked vs failed** | Environment failures were reported as `FAIL`. | Infrastructure failures return `BLOCKED` and are excluded from the defect log. |
| 6 | **Severity scale** | Four internal levels vs the three-level defect scale. | `severity_map` maps `Critical → High` for reporting. |
| 7 | **Execution order** | No documented triage sequence. | Execution Order column added; documented above. |
| 8 | **Config errors one at a time** | Setup took several attempts. | All missing variables reported in one `ConfigError`; exit code `2`. |
| 9 | **Regression evidence** | Seeded-defect detection was asserted, not shown. | Mock replica + harness; 17/17 detected. |
| 10 | **Rounding false positives** | Stored averages and rendered totals are rounded. | Separate configurable tolerances for averages and displayed values. |

---

## 9. Dependencies

**Python packages** (`requirements.txt`): pandas, numpy, SQLAlchemy, PyMySQL, cryptography,
requests, beautifulsoup4, lxml, html5lib, PyYAML, python-dotenv, openpyxl, pytest.

**Environment**: MySQL hosting the six schemas with `SELECT` for `learner_user`; the Online Sales
API on port 5001 with a valid `X-API-Key`; the reporting application on port 5002; and the ETL
load for the business date completed before execution.

---

## 10. Assumptions

1. QA has **read-only** access to all six schemas.
2. Only `COMPLETED` (Retail/Online) and `APPROVED` (Distributor) records should reach staging.
3. `product_master_raw` and `branch_region_mapping_raw` are the single sources of truth for
   product harmonisation and region derivation.
4. `net_sales_amount = gross_sales_amount − discount_amount` (no reversal column).
5. `sales_transaction_id` is a prefixed surrogate key whose trailing segment is the source
   `transaction_id`; DM-V06 relies on this to join retail rows back to staging.
6. All timestamps are IST (UTC+5:30) unless the API supplies an explicit offset.
7. Currency tolerance **0.01**, average tolerance **0.5**, UNKNOWN_REGION threshold **5%** — all
   configurable in `validation_rules.yaml`.

---

## 11. Known limitations

1. **Dashboard extraction** parses HTML tables and labelled numeric values. If the reporting app
   renders values via JavaScript, a headless-browser step would be needed.
2. **The mock harness is a smoke test**, not a substitute for lab execution. It proves the code
   runs and detects seeded defects; real findings must come from `python -m src.main`.
3. **DM-V02/V03** join staging to the data mart via `product_code` because the data mart does not
   retain `product_type_raw`. If one product code ever carried two different raw labels, the check
   would need the transaction-level key instead.
4. **Full-volume field comparison** loads a day's data into memory; batch by date for large ranges.
5. **Defect logging is file-based** in this phase; JIRA/Zephyr integration is a later step.

---

*Prepared by Parashurama Nagalapurada — Senior Associate, Quality Assurance*
