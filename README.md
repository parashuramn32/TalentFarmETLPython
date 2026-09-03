# Python QA Automation — Financial Services Sales Data Pipeline

**Submission 2 — Test Design and Automation Build**

A modular, configuration-driven Python validation suite that verifies data accuracy across every
layer of the sales data pipeline: **Source → Staging → Data Mart → Aggregates → Reporting**.

All table and column names are **verified against the project DDL scripts**
(`01Cretaedatabase` – `04CreateReportingViews`). Every automated check is traceable to a QA risk
identified in Submission 1.

---

## 1. Verified schema

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
  reconcile exactly** (row count and totals).

---

## 2. Coverage

| Layer | Test cases | Coverage |
|---|---|---|
| **Source-to-Staging** | 32 | Counts, status filters, duplicates, key/rename integrity, nulls, schema, data types, field-level comparison, business date, load lineage, API health/auth/pagination/JSON schema/date range |
| **Data Quality** | 10 | Mobile & state formats, KYC compliance, DOB plausibility, domain values, orphan product/customer/branch references, conditional mandatory fields |
| **Staging-to-Data Mart** | 16 | Product harmonisation (incl. ULIP), name/category standardisation, channel mapping, region derivation for all three channels, customer cleansing, state enrichment, net-amount recomputation, inactive products, count reconciliation, surrogate key uniqueness |
| **Aggregate Models** | 9 | Region / channel / product / daily / executive summaries, average ticket size, top region & channel, cross-summary consistency |
| **Data Mart-to-Reporting** | 12 | All five views vs data mart, all five dashboard pages, filters, freshness, cross-report reconciliation |
| **Regression** | 3 | Post-fix, per-load, and seeded-defect detection runs |
| **Total** | **82** | Covering **54 risks** with full bidirectional traceability |

---

## 3. Project structure

```
qa_automation/
├── config/
│   ├── db_config.yaml           # 6 MySQL schemas (env placeholders)
│   ├── api_config.yaml          # Online Sales API settings
│   ├── source_config.yaml       # Source objects, filters, mandatory & compare columns
│   ├── validation_rules.yaml    # Column contracts, harmonisation matrix, patterns, tolerance
│   └── reporting_config.yaml    # 5 views, dashboards and measures
├── src/
│   ├── main.py                  # CLI entry point / orchestrator
│   ├── connectors/
│   │   ├── db_connector.py      # MySQL (SQLAlchemy, read-only)
│   │   ├── file_connector.py    # Retail CSV reader
│   │   ├── api_connector.py     # REST API + pagination + 429 backoff
│   │   └── report_connector.py  # Flask report page extraction
│   ├── validators/
│   │   ├── base_validator.py    # Reusable primitives
│   │   ├── source_to_staging.py # + master / reference data quality
│   │   ├── staging_to_datamart.py
│   │   ├── aggregate_validator.py
│   │   └── reporting_validator.py
│   └── utils/
│       ├── config_loader.py     # YAML + ${ENV_VAR} resolution
│       ├── logger.py
│       ├── result.py            # ValidationResult model
│       └── report_generator.py  # Excel / HTML / console reports
├── tests/
│   ├── test_base_validator.py   # 20 unit tests (no DB/API needed)
│   └── generate_sample_run.py   # Produces sample execution output
├── reports/                     # Generated validation reports
├── logs/                        # Execution logs
├── requirements.txt
├── .env.sample
└── README.md
```

---

## 4. Setup

### 4.1 Prerequisites
- Python 3.9+
- `learner_user` read-only access to the six MySQL schemas
- Online Sales API on port **5001**, reporting app on port **5002**

### 4.2 Install

```bash
cd qa_automation
python -m venv venv

# Windows
venv\Scripts\activate
# Linux / macOS
source venv/bin/activate

pip install -r requirements.txt
```

### 4.3 Configure credentials

**No credentials are stored in code or config.** All sensitive values resolve from environment
variables at runtime.

```bash
cp .env.sample .env      # Windows: copy .env.sample .env
```

```
DB_HOST=127.0.0.1
DB_USER=learner_user
DB_PASSWORD=your-password
API_BASE_URL=http://127.0.0.1:5001/api
API_KEY=your-api-key
REPORT_BASE_URL=http://127.0.0.1:5002
RETAIL_CSV_PATH=C:/QA-Automation/tf-financial-sales-env/source_files/retail_sales_raw.csv
```

> ⚠️ `.env` must never be committed to source control.

---

## 5. Running the suite

```bash
# 1. Verify connectivity to all six schemas and the API
python -m src.main --health-check

# 2. Full run for a business date
python -m src.main --date 2026-05-01

# 3. Single layer
python -m src.main --date 2026-05-01 --layer source
python -m src.main --date 2026-05-01 --layer master
python -m src.main --date 2026-05-01 --layer datamart
python -m src.main --date 2026-05-01 --layer aggregate
python -m src.main --date 2026-05-01 --layer reporting

# 4. Regression (every layer)
python -m src.main --date 2026-05-01 --regression

# 5. Read the retail source from the CSV file instead of retail_sales_raw
python -m src.main --date 2026-05-01 --use-csv

# 6. Unit tests
python -m pytest tests/ -v

# 7. Regenerate sample output (no DB/API required)
python tests/generate_sample_run.py
```

**Exit codes:** `0` = all checks passed · `1` = one or more failures/errors.

---

## 6. Outputs

| Output | Location |
|---|---|
| Excel report (Summary, Results, Defects, By Layer) | `reports/validation_report_<timestamp>.xlsx` |
| HTML report (colour-coded dashboard) | `reports/validation_report_<timestamp>.html` |
| Console summary | stdout |
| Execution log | `logs/execution_<YYYYMMDD>.log` |

Every result records Test Case ID, layer, description, **risk reference**, source object,
target object, expected, actual, status, severity, message and a failed-row sample.

---

## 7. Sample execution output

Included in `reports/`:

- `sample_validation_report.xlsx`
- `sample_validation_report.html`
- `sample_console_output.log`

The sample run executes **82 validations** and demonstrates the suite correctly
**detecting seeded defects**, including:

| Detected defect | Test case |
|---|---|
| CANCELLED transaction leaked into staging | RET-V02 |
| Staging count inflated vs source | RET-V01 / RET-V04 |
| Distributor region_code not in reference data | DST-V09 |
| Invalid mobile number formats | MST-V02 |
| Unresolvable customer state values | MST-V03 |
| REJECTED-KYC customers contributing to net sales | MST-V04 |
| Missing folio_number on mutual fund sales | MST-V12 |
| Retail branch_code missing from mapping | MST-V13 |
| ULIP mis-mapped to MUTUAL_FUND | DM-V01 / DM-V02 / DM-V03 |
| Distributor & online region derivation errors | DM-V07 / DM-V08 |
| Sales against inactive products (`active_flag = 0`) | DM-V13 |
| Null-customer transactions counted as net sales | DM-V15 |
| Product & daily summary aggregate mismatches | AGG-V04 / AGG-V05 |
| Insurance / mutual fund split distorted | AGG-V06 |
| Summary models disagreeing | AGG-V08 |
| Region page total ≠ reporting view | RPT-V05 |

---

## 8. Design notes

- **Modular, not monolithic** — connectors, validators and reporting are separate; layer
  validators reuse primitives from `base_validator.py`.
- **Configuration-driven** — filters, mappings, tolerances and **column contracts** live in YAML.
  If a column is renamed, only `validation_rules.yaml` needs editing.
- **No hardcoded secrets** — credentials, URLs and paths resolve from environment variables.
- **API-aware** — full pagination, 401 handling, HTTP 429 backoff/retry.
- **Traceable** — every result carries a `risk_ref` linking back to the Submission 1 risk list.
- **Tolerance-based numeric comparison** — currency compared at 0.01 to avoid false rounding failures.
- **Graceful skips** — checks return `SKIPPED` (not `FAIL`) when an optional column or dataset is
  unavailable, keeping the pass rate meaningful.

---

## 9. Dependencies

### 9.1 Python packages (`requirements.txt`)

| Package | Purpose |
|---|---|
| `pandas`, `numpy` | DataFrame comparison, aggregation and recomputation |
| `SQLAlchemy`, `PyMySQL`, `cryptography` | Read-only MySQL connectivity to the six schemas |
| `requests` | Online Sales API calls (pagination, auth, retry) |
| `beautifulsoup4`, `lxml`, `html5lib` | Parsing rendered dashboard pages |
| `PyYAML`, `python-dotenv` | Configuration loading and environment resolution |
| `openpyxl`, `XlsxWriter` | Excel validation report generation |
| `pytest` | Unit tests for the validation primitives |

### 9.2 Environment dependencies

- MySQL instance hosting `fs_source_retail`, `fs_source_distributor`, `fs_source_online`,
  `fs_staging`, `fs_datamart`, `fs_reporting`
- `learner_user` with `SELECT` privileges on all six schemas
- Online Sales API reachable on port **5001** with a valid `X-API-Key`
- Reporting application reachable on port **5002**
- The ETL load for the business date under test must have completed before execution

---

## 10. Assumptions

1. QA has **read-only** (`learner_user`) access to all six schemas.
2. Only `COMPLETED` (Retail/Online) and `APPROVED` (Distributor) records should reach staging.
3. `product_master_raw` and `branch_region_mapping_raw` are the single sources of truth for
   product harmonisation and region derivation.
4. `net_sales_amount = gross_sales_amount − discount_amount` (no reversal column in this schema).
5. `sales_transaction_id` is a prefixed surrogate key whose trailing segment is the source
   `transaction_id`; DM-V06 relies on this to join retail rows back to staging.
6. All timestamps are IST (UTC+5:30) unless the API supplies an explicit offset.
7. Currency comparison tolerance is **0.01**; the UNKNOWN_REGION threshold is **5%** of volume.

---

## 11. Known limitations

1. **Dashboard extraction** parses HTML tables and labelled numeric values. If the reporting app
   renders values via JavaScript, a headless-browser step (Selenium/Playwright) would be needed.
2. **Sample output is mock-generated** — it demonstrates framework correctness without live lab
   connectivity. Re-run `python -m src.main --date <date>` inside the lab VM for live evidence.
3. **Raw product label checks (DM-V02/V03)** join staging to the data mart via `product_code`
   because `dm_sales_transaction` does not retain `product_type_raw`. If one product code ever
   carried two different raw labels, the check would need the transaction-level key instead.
4. **Full-volume field comparison** loads a day's data into memory; batch by date for large ranges.
5. **Defect logging is file-based** in this phase; JIRA/Zephyr integration is planned for Submission 3.

---

*Prepared by Parashurama Nagalapurada — Senior Associate, Quality Assurance*
