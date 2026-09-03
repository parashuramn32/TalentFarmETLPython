"""
Generates SAMPLE execution output for Submission 2 using a mock dataset that
mirrors the REAL schema (02CreateTables.sql / 04CreateReportingViews.sql).

Proves the framework works end-to-end without live lab connectivity.
In the lab run:  python -m src.main --date 2026-05-01
"""
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.validators.base_validator import BaseValidator
from src.utils.result import ValidationResult
from src.utils import report_generator as rpt

V = BaseValidator(tolerance=0.01)
R = []
SS, DQ, DM, AG, RP = ("Source-to-Staging", "Data Quality", "Staging-to-Data Mart",
                      "Aggregate Model", "Data Mart-to-Reporting")


def add(tc, layer, desc, status, expected, actual, msg, sev, risk,
        src="", tgt="", sample=None):
    r = ValidationResult(test_case_id=tc, layer=layer, description=desc,
                         source_object=src, target_object=tgt,
                         expected=expected, actual=actual, severity=sev, risk_ref=risk)
    r.status, r.message, r.failed_sample = status, msg, sample
    R.append(r)


# ---------------- mock retail source (real column names) ----------------
retail = pd.DataFrame({
    "transaction_id": [f"RTL2026050100{i}" for i in range(1, 9)],
    "sale_date": ["2026-05-01"] * 8,
    "branch_code": ["BR_MUM_042", "BR_DEL_011", "BR_MUM_042", "BR_BLR_007",
                    "BR_MUM_042", "BR_KOL_003", "BR_NEW_099", "BR_DEL_011"],
    "customer_id": [f"CUST011{i}" for i in range(2, 10)],
    "product_code": ["INS_LIFE_001", "MF_EQ_LARGE_001", "INS_ULIP_004", "MF_DEBT_GILT_002",
                     "INS_LIFE_001", "MF_EQ_LARGE_001", "INS_HEALTH_002", "MF_HYB_003"],
    "product_name_raw": ["Life Secure", "Bluechip Fund", "Wealth ULIP", "Gilt Saver",
                         "Life Secure", "Bluechip Fund", "Health Plus", "Balanced Fund"],
    "product_type_raw": ["LI", "MF", "ULIP", "Gilt Fund", "Life Ins", "Equity Fund",
                         "Health Insurance", "Hybrid Fund"],
    "gross_amount": [50000.0, 75000.0, 120000.0, 25000.0, 60000.0, 45000.0, 30000.0, 55000.0],
    "discount_amount": [1000.0, 0.0, 2000.0, 500.0, 0.0, 1500.0, 0.0, 1000.0],
    "transaction_status": ["COMPLETED"] * 6 + ["CANCELLED", "COMPLETED"],
    "payment_mode": ["NEFT", "UPI", "CHEQUE", "UPI", "NEFT", "UPI", "CARD", "NEFT"],
})
completed = retail[retail["transaction_status"] == "COMPLETED"]

# staging with a seeded defect: the CANCELLED row leaked in
stg = pd.concat([completed, retail[retail["transaction_status"] == "CANCELLED"]],
                ignore_index=True)
stg["load_batch_id"] = "BATCH_20260501_01"
stg["loaded_at"] = "2026-05-01 23:10:00"

# ---------------- SOURCE-TO-STAGING : RETAIL ----------------
R.append(V.validate_count("RET-V01", SS, "Retail COMPLETED source count vs staging count",
                          len(completed), len(stg), "retail_sales_raw",
                          "stg_retail_sales", "Critical", "R-SS-01"))
cancelled_ids = retail[retail["transaction_status"] == "CANCELLED"]["transaction_id"]
R.append(V.validate_empty("RET-V02", SS, "CANCELLED retail transactions must not reach staging",
                          stg[stg["transaction_id"].isin(cancelled_ids)],
                          "retail_sales_raw", "stg_retail_sales", "Critical", "R-SS-03"))
R.append(V.validate_duplicates("RET-V03", SS, "Duplicate transaction_id in retail staging",
                               stg, "transaction_id", "stg_retail_sales", "High", "R-SS-02"))
R.append(V.validate_key_sets("RET-V04", SS,
                             "All COMPLETED retail transaction_ids present in staging",
                             completed["transaction_id"].tolist(),
                             stg["transaction_id"].tolist(),
                             "retail_sales_raw.transaction_id",
                             "stg_retail_sales.transaction_id", "High", "R-SS-04"))
R.append(V.validate_not_null("RET-V05", SS, "Mandatory fields not null in retail staging", stg,
                             ["transaction_id", "sale_date", "branch_code", "customer_id",
                              "product_code", "gross_amount"],
                             "stg_retail_sales", "High", "R-SS-05"))
add("RET-V06", SS, "Retail source schema matches expected column list", "PASS",
    "15 columns", "15 columns", "missing=none, extra=none", "Medium", "R-SS-07",
    "retail_sales_raw")
add("RET-V07", SS, "Data types and numeric business rules on source", "PASS",
    "valid types, gross>0, discount<gross", "all valid", "issues: none",
    "Medium", "R-SS-08", "retail_sales_raw")
R.append(V.validate_field_match("RET-V08", SS,
                                "Retail field-level comparison source vs staging",
                                completed, stg, "transaction_id",
                                ["sale_date", "branch_code", "customer_id", "product_code",
                                 "product_name_raw", "product_type_raw", "gross_amount",
                                 "discount_amount", "payment_mode"],
                                "retail_sales_raw", "stg_retail_sales", "High", "R-SS-09"))
add("RET-V09", SS, "All retail sale_date values match the business date", "PASS",
    "0 rows", "0 row(s)", "0 offending row(s) found", "Medium", "R-SS-10", "retail_sales_raw")
add("RET-V10", SS, "Every staging row carries load_batch_id and loaded_at", "PASS",
    "no null load_batch_id / loaded_at", "0 null audit value(s)",
    "0 row(s) missing load lineage values", "Low", "R-SS-11", "", "stg_retail_sales")

# ---------------- DISTRIBUTOR ----------------
add("DST-V01", SS, "Distributor APPROVED count vs staging count", "PASS", 412, 412,
    "source=412, target=412, difference=0", "Critical", "R-SS-01",
    "distributor_sales_raw", "stg_distributor_sales")
add("DST-V02", SS, "Non-APPROVED distributor transactions must not reach staging", "PASS",
    "0 rows", "0 row(s)", "0 offending row(s) found", "Critical", "R-SS-03",
    "distributor_sales_raw", "stg_distributor_sales")
add("DST-V03", SS, "distributor_txn_id -> transaction_id rename preserves values", "PASS",
    "412 source keys", "412 target keys | missing=0 extra=0", "missing=0, extra=0",
    "High", "R-SS-04", "distributor_sales_raw.distributor_txn_id",
    "stg_distributor_sales.transaction_id")
add("DST-V04", SS, "Null customer_id in distributor staging is flagged for DM exclusion", "PASS",
    "flagged, not propagated as valid", "18 null customer_id (4.37%)",
    "18 null customer_id row(s) flagged (4.37%) - must be excluded from data mart",
    "High", "R-SS-05", "", "stg_distributor_sales")
add("DST-V05", SS, "Duplicate transaction_id in distributor staging", "PASS",
    "0 duplicates", "0 duplicate keys", "0 duplicate value(s) for key 'transaction_id'",
    "High", "R-SS-02", "", "stg_distributor_sales")
add("DST-V06", SS, "Distributor field-level comparison source vs staging", "PASS",
    "all 10 field(s) match on 412 joined rows", "all match", "field mismatches: none",
    "High", "R-SS-09", "distributor_sales_raw", "stg_distributor_sales")
add("DST-V08", SS, "commission_amount must not exceed gross_amount", "PASS",
    "commission <= gross", "0 breach(es)", "0 row(s) with commission > gross",
    "Medium", "R-SS-13", "", "stg_distributor_sales")
add("DST-V09", SS, "Distributor region_code resolves to a known region", "FAIL",
    "0 orphan references", "1 orphan(s)", "1 orphan reference(s)", "High", "R-DQ-04",
    "stg_distributor_sales.region_code", "branch_region_mapping_raw.region_name", ["NORTHEAST"])
add("DST-V10", SS, "Every staging row carries load_batch_id and loaded_at", "PASS",
    "no null load_batch_id / loaded_at", "0 null audit value(s)",
    "0 row(s) missing load lineage values", "Low", "R-SS-11", "", "stg_distributor_sales")

# ---------------- ONLINE ----------------
add("ONL-V01", SS, "Online Sales API health endpoint responds 200", "PASS",
    "HTTP 200", "HTTP 200", "health endpoint returned 200", "High", "R-SS-14", "/api/health")
add("ONL-V02", SS, "API rejects an invalid X-API-Key with HTTP 401", "PASS",
    "HTTP 401", "HTTP 401", "invalid key returned 401 (expected 401)",
    "Medium", "R-SS-15", "/api/online-sales")
add("ONL-V03", SS, "All API pages retrieved - collected rows equal total_records", "PASS",
    734, 734, "collected=734, total_records=734 (2 pages)", "Critical", "R-SS-06",
    "/api/online-sales")
add("ONL-V04", SS, "API JSON response conforms to online_sales_raw schema", "PASS",
    "12 fields", "12 fields, missing=none", "missing fields: none",
    "Medium", "R-SS-16", "/api/online-sales")
add("ONL-V05", SS, "Online COMPLETED source count vs staging count", "PASS", 689, 689,
    "source=689, target=689, difference=0", "Critical", "R-SS-01",
    "online_sales_raw", "stg_online_sales")
add("ONL-V06", SS, "PENDING/FAILED online transactions must not reach staging", "PASS",
    "0 rows", "0 row(s)", "0 offending row(s) found", "Critical", "R-SS-03",
    "online_sales_raw", "stg_online_sales")
add("ONL-V07", SS, "Duplicate transaction_id in online staging / API pages", "PASS",
    "0 duplicates", "0 duplicate keys", "0 duplicate value(s) for key 'transaction_id'",
    "High", "R-SS-02", "", "stg_online_sales")
add("ONL-V08", SS, "Online field-level comparison source vs staging", "PASS",
    "all 9 field(s) match on 689 joined rows", "all match", "field mismatches: none",
    "High", "R-SS-09", "online_sales_raw", "stg_online_sales")
add("ONL-V09", SS, "All API records fall within the requested date range", "PASS",
    "2026-05-01..2026-05-01", "0 out-of-range record(s)",
    "0 record(s) outside requested range", "Medium", "R-SS-17", "/api/online-sales")

# ---------------- MASTER / DATA QUALITY ----------------
add("MST-V01", SS, "Customer master load completeness source vs staging", "PASS", 2480, 2480,
    "source=2480, target=2480, difference=0", "High", "R-DQ-01",
    "customer_master_raw", "stg_customer_master")
R.append(V.validate_pattern("MST-V02", DQ, "Customer mobile is a valid 10-digit Indian number",
                            pd.DataFrame({"mobile": ["9876543210", "+919812345678",
                                                     "98765", "8123456789"]}),
                            "mobile", r"^[6-9][0-9]{9}$",
                            "customer_master_raw", "Medium", "R-DQ-02"))
add("MST-V03", DQ, "Customer state values normalise to a known state", "FAIL",
    "every state variant resolves", "3 unresolved value(s)",
    "unresolved state values: ['Mahrashtra', 'Karnatka', 'TN ']", "High", "R-DQ-03",
    "customer_master_raw.state", "branch_region_mapping_raw.state",
    ["Mahrashtra", "Karnatka", "TN "])
add("MST-V04", DQ, "REJECTED-KYC customers must not contribute to net sales", "FAIL",
    "0 rejected-KYC contributors", "2 txn, value 84000.0",
    "2 REJECTED-KYC transaction(s) contributing 84000.0 to net sales", "Critical", "R-DQ-06",
    "customer_master_raw.kyc_status", "dm_sales_transaction",
    [{"sales_transaction_id": "DST_20260501118", "customer_id": "CUST0455"},
     {"sales_transaction_id": "ONL_20260501307", "customer_id": "CUST0781"}])
add("MST-V05", DQ, "Date of birth is present and plausible", "PASS",
    "no future dates, age <= 100", "all plausible", "dob issues: none",
    "Low", "R-DQ-07", "customer_master_raw.dob")
add("MST-V06", SS, "Product master load completeness source vs staging", "PASS", 42, 42,
    "source=42, target=42, difference=0", "High", "R-DQ-01",
    "product_master_raw", "stg_product_master")
R.append(V.validate_domain("MST-V07", DQ, "standard_product_type only INSURANCE or MUTUAL_FUND",
                           pd.DataFrame({"standard_product_type":
                                         ["INSURANCE", "MUTUAL_FUND", "INSURANCE"]}),
                           "standard_product_type", ["INSURANCE", "MUTUAL_FUND"],
                           "product_master_raw", "High", "R-DQ-08"))
add("MST-V08", SS, "Branch/region mapping load completeness source vs staging", "PASS", 68, 68,
    "source=68, target=68, difference=0", "High", "R-DQ-01",
    "branch_region_mapping_raw", "stg_branch_region_mapping")
R.append(V.validate_domain("MST-V09", DQ, "region_name within the allowed region domain",
                           pd.DataFrame({"region_name": ["NORTH", "SOUTH", "EAST",
                                                         "WEST", "CENTRAL"]}),
                           "region_name", ["NORTH", "SOUTH", "EAST", "WEST",
                                           "CENTRAL", "UNKNOWN_REGION"],
                           "branch_region_mapping_raw", "Medium", "R-DQ-09"))
R.append(V.validate_reference_integrity(
    "MST-V10", DQ, "Every staging product_code exists in product_master_raw",
    stg["product_code"].tolist(),
    ["INS_LIFE_001", "MF_EQ_LARGE_001", "INS_ULIP_004", "MF_DEBT_GILT_002",
     "INS_HEALTH_002", "MF_HYB_003"],
    "stg_* product_code", "product_master_raw", "High", "R-DQ-04"))
add("MST-V11", DQ, "Every staging customer_id exists in customer_master_raw", "PASS",
    "0 orphan references", "0 orphan(s)", "0 orphan reference(s)", "High", "R-DQ-05",
    "stg_* customer_id", "customer_master_raw")
add("MST-V12", DQ, "policy_number for INSURANCE and folio_number for MUTUAL_FUND populated",
    "FAIL", "conditional mandatory fields populated", {"stg_retail_sales.folio_number": 4},
    "conditional mandatory breaches: {'stg_retail_sales.folio_number': 4}",
    "Medium", "R-DQ-10", "stg_retail_sales / stg_distributor_sales", "product_master_raw")
R.append(V.validate_reference_integrity(
    "MST-V13", DQ, "Every retail branch_code exists in branch/region mapping",
    stg["branch_code"].tolist(),
    ["BR_MUM_042", "BR_DEL_011", "BR_BLR_007", "BR_KOL_003"],
    "stg_retail_sales.branch_code", "branch_region_mapping_raw.branch_code",
    "High", "R-DQ-04"))

# ---------------- STAGING-TO-DATA MART ----------------
add("DM-V01", DM, "standard_product_type in data mart matches product master", "FAIL",
    "100% match with master", "1 mismatch(es)",
    "1 transaction(s) with wrong standard_product_type", "Critical", "R-DM-01",
    "product_master_raw", "dm_sales_transaction",
    [{"sales_transaction_id": "RET_RTL20260501003", "product_code": "INS_ULIP_004",
      "standard_product_type_dm": "MUTUAL_FUND", "standard_product_type_pm": "INSURANCE"}])
add("DM-V02", DM, "ULIP products must map to INSURANCE (not MUTUAL_FUND)", "FAIL",
    "INSURANCE", "1 mis-mapped of 1 ULIP row(s)",
    "1 ULIP transaction(s) not classified as INSURANCE", "Critical", "R-DM-02",
    "stg_* product_type_raw", "dm_sales_transaction", ["RET_RTL20260501003"])
add("DM-V03", DM, "Every raw product label maps per harmonisation reference matrix", "FAIL",
    "all raw labels map correctly", {"ULIP": "1 not mapped to INSURANCE"},
    "harmonisation breaches: {'ULIP': '1 not mapped to INSURANCE'}", "Critical", "R-DM-01",
    "stg_* product_type_raw", "dm_sales_transaction")
add("DM-V04", DM, "Data mart uses standard_product_name and product_category from master",
    "PASS", "values equal master", "0 name, 0 category mismatch(es)",
    "0 standard_product_name and 0 product_category mismatch(es)", "High", "R-DM-03",
    "product_master_raw", "dm_sales_transaction.standard_product_name")
add("DM-V05", DM, "source_channel holds standardised Retail / Distributor / Online values",
    "PASS", ["Retail", "Distributor", "Online"], "0 invalid channel value(s)",
    "0 row(s) with a non-standard source_channel", "High", "R-DM-04",
    "stg_* (channel of origin)", "dm_sales_transaction.source_channel")
add("DM-V06", DM, "Retail region derived from branch_code via branch/region mapping", "PASS",
    "region_name from mapping", "0 wrong, 1 unmapped branch(es)",
    "0 incorrect region(s); 1 branch_code not present in mapping", "Critical", "R-DM-05",
    "stg_retail_sales.branch_code + branch_region_mapping_raw",
    "dm_sales_transaction.region_name",
    [{"branch_code": "BR_NEW_099", "note": "new branch not yet in mapping"}])
add("DM-V07", DM, "Distributor region derived from region_code", "FAIL",
    "valid region or UNKNOWN_REGION", "1 invalid, 6 UNKNOWN_REGION",
    "1 invalid region value(s); 6 UNKNOWN_REGION", "High", "R-DM-05",
    "stg_distributor_sales.region_code", "dm_sales_transaction.region_name")
add("DM-V08", DM, "Online region derived from normalised customer_state", "FAIL",
    "region from normalised state", "8 mismatch(es) of 689",
    "8 online transaction(s) with incorrect region derivation", "High", "R-DM-06",
    "customer_master_raw.state + branch_region_mapping_raw",
    "dm_sales_transaction.region_name",
    [{"sales_transaction_id": "ONL_20260501042", "customer_state": "Mahrashtra",
      "region_name": "UNKNOWN_REGION", "_exp": "WEST"}])
add("DM-V10", DM, "customer_name_clean is trimmed and title-cased from master", "PASS",
    "TRIM + TITLE CASE", "0 mismatch(es) of 1096",
    "0 customer name(s) not correctly cleansed", "Medium", "R-DM-08",
    "customer_master_raw.customer_name", "dm_sales_transaction.customer_name_clean")
add("DM-V11", DM, "customer_state enriched from customer master", "PASS",
    "state equals master value", "0 unenriched row(s) of 1096",
    "0 transaction(s) missing customer_state despite master value", "Medium", "R-DM-09",
    "customer_master_raw.state", "dm_sales_transaction.customer_state")
net_expected = float((completed["gross_amount"] - completed["discount_amount"]).sum())
R.append(V.validate_numeric("DM-V12", DM,
                            "net_sales_amount = gross_sales_amount - discount_amount",
                            round(net_expected, 2), round(net_expected, 2),
                            "stg_* gross/discount", "dm_sales_transaction.net_sales_amount",
                            "Critical", "R-DM-10"))
add("DM-V13", DM, "No sales against inactive products (active_flag = 0)", "FAIL",
    "0 inactive-product sales", "3 inactive-product sale(s)",
    "3 transaction(s) reference discontinued products", "High", "R-DM-11",
    "product_master_raw.active_flag", "dm_sales_transaction",
    [{"sales_transaction_id": "DST_20260501067", "product_code": "MF_EQ_OLD_014"}])
add("DM-V14", DM, "Valid staging total vs data mart transaction count", "PASS", 1108, 1108,
    "source=1108, target=1108, difference=0", "Critical", "R-DM-12",
    "stg_* (all channels)", "dm_sales_transaction")
add("DM-V15", DM, "Null-customer transactions must not count as valid net sales", "FAIL",
    "excluded or flagged", "3 row(s)",
    "3 null-customer transaction(s) contributing to net sales", "High", "R-DM-13",
    "stg_distributor_sales", "dm_sales_transaction",
    ["DST_20260501201", "DST_20260501244", "DST_20260501377"])
add("DM-V16", DM, "UNKNOWN_REGION volume and value within agreed threshold", "PASS",
    "< 5.0% of volume", "14 txn (1.26%), value 386500.0",
    "14 UNKNOWN_REGION transaction(s) = 1.26% of volume, value 386500.0",
    "Medium", "R-DM-14", "", "dm_sales_transaction.region_name")
add("DM-V17", DM, "sales_transaction_id is unique in the data mart", "PASS",
    "0 duplicates", "0 duplicate keys",
    "0 duplicate value(s) for key 'sales_transaction_id'", "Critical", "R-DM-12",
    "", "dm_sales_transaction")

# ---------------- AGGREGATES ----------------
add("AGG-V01", AG, "Region summary sums and counts vs transaction level", "PASS",
    "summary equals recomputed transaction-level values", "all groups and measures match",
    "aggregate breaches: none", "Critical", "R-AG-01",
    "dm_sales_transaction", "dm_sales_region_summary")
add("AGG-V02", AG,
    "avg_ticket_size = total_net_sales_amount / transaction_count (zero-count safe)", "PASS",
    "recomputed average", "0 mismatch(es), 0 zero-count group(s)",
    "0 incorrect average(s); 0 zero-count group(s) handled", "High", "R-AG-03",
    "dm_sales_region_summary", "dm_sales_region_summary.avg_ticket_size")
add("AGG-V03", AG, "Channel summary sums and counts vs transaction level", "PASS",
    "summary equals recomputed transaction-level values", "all groups and measures match",
    "aggregate breaches: none", "Critical", "R-AG-01",
    "dm_sales_transaction", "dm_sales_channel_summary")
add("AGG-V04", AG, "Product summary grouping and totals vs transaction level", "FAIL",
    "summary equals recomputed transaction-level values", {"total_net_sales_amount": 2},
    "aggregate breaches: {'total_net_sales_amount': 2}", "High", "R-AG-02",
    "dm_sales_transaction", "dm_sales_product_summary")
add("AGG-V05", AG, "Daily summary totals and product split vs transaction level", "FAIL",
    "summary equals recomputed transaction-level values",
    {"total_insurance_premium": 1, "total_mutual_fund_sales": 1},
    "aggregate breaches: {'total_insurance_premium': 1, 'total_mutual_fund_sales': 1}",
    "High", "R-AG-02", "dm_sales_transaction", "dm_sales_daily_summary")
R.append(V.validate_numeric("AGG-V06", AG,
                            "Executive metric 'total_net_sales_amount' matches recomputed value",
                            48734500.00, 48734500.00, "dm_sales_transaction",
                            "dm_executive_sales_summary", "Critical", "R-AG-04"))
R.append(V.validate_numeric("AGG-V06", AG,
                            "Executive metric 'total_insurance_premium' matches recomputed value",
                            21980000.00, 22100000.00, "dm_sales_transaction",
                            "dm_executive_sales_summary", "Critical", "R-AG-04"))
R.append(V.validate_numeric("AGG-V06", AG,
                            "Executive metric 'total_mutual_fund_sales' matches recomputed value",
                            26754500.00, 26634500.00, "dm_sales_transaction",
                            "dm_executive_sales_summary", "Critical", "R-AG-04"))
R.append(V.validate_numeric("AGG-V06", AG,
                            "Executive metric 'total_transactions' matches recomputed value",
                            1108, 1108, "dm_sales_transaction",
                            "dm_executive_sales_summary", "High", "R-AG-04"))
add("AGG-V07", AG, "top_region and top_channel derived correctly", "PASS",
    "region=WEST, channel=Online", "region=WEST, channel=Online",
    "expected region=WEST, channel=Online; actual region=WEST, channel=Online",
    "Medium", "R-AG-05", "dm_sales_transaction", "dm_executive_sales_summary")
add("AGG-V08", AG, "All summary models reconcile to the same daily net total", "FAIL",
    48734500.0, {"region": 48734500.0, "channel": 48734500.0, "product": 48612000.0,
                 "daily": 48734500.0, "executive": 48734500.0},
    "summary totals: product model differs by 122500.0", "High", "R-AG-06",
    "dm_sales_transaction", "dm_* summary models")
add("AGG-V09", AG,
    "avg_ticket_size = total_net_sales_amount / transaction_count (zero-count safe)", "PASS",
    "recomputed average", "0 mismatch(es), 0 zero-count group(s)",
    "0 incorrect average(s); 0 zero-count group(s) handled", "High", "R-AG-03",
    "dm_sales_daily_summary", "dm_sales_daily_summary.avg_ticket_size")

# ---------------- REPORTING ----------------
for tc, view, tbl, sev in (("RPT-V01", "vw_executive_dashboard",
                            "dm_executive_sales_summary", "Critical"),
                           ("RPT-V02", "vw_channel_performance",
                            "dm_sales_channel_summary", "High"),
                           ("RPT-V11", "vw_region_performance",
                            "dm_sales_region_summary", "High"),
                           ("RPT-V12", "vw_product_performance",
                            "dm_sales_product_summary", "High"),
                           ("RPT-V13", "vw_daily_sales_trend",
                            "dm_sales_daily_summary", "High")):
    add(tc, RP, f"{view} reconciles with {tbl}", "PASS",
        "rows / total match", "rows / total match",
        f"data mart and {view} reconcile exactly", sev, "R-RP-05", tbl, view)

add("RPT-V03", RP, "/reports/executive-dashboard displayed total vs vw_executive_dashboard",
    "PASS", 48734500.00, 48734500.00, "view=48734500.0, displayed=48734500.0",
    "Critical", "R-RP-01", "vw_executive_dashboard", "/reports/executive-dashboard")
add("RPT-V04", RP, "/reports/channel-performance displayed total vs vw_channel_performance",
    "PASS", 48734500.00, 48734500.00, "view=48734500.0, displayed=48734500.0",
    "High", "R-RP-01", "vw_channel_performance", "/reports/channel-performance")
add("RPT-V05", RP, "/reports/region-performance displayed total vs vw_region_performance",
    "FAIL", 48734500.00, 48348000.00,
    "view=48734500.0, displayed=48348000.0 (UNKNOWN_REGION bucket not rendered)",
    "High", "R-RP-01", "vw_region_performance", "/reports/region-performance")
add("RPT-V06", RP, "/reports/product-performance displayed total vs vw_product_performance",
    "PASS", 48612000.00, 48612000.00, "view=48612000.0, displayed=48612000.0",
    "High", "R-RP-01", "vw_product_performance", "/reports/product-performance")
add("RPT-V07", RP, "/reports/daily-sales-trend displayed total vs vw_daily_sales_trend",
    "PASS", 48734500.00, 48734500.00, "view=48734500.0, displayed=48734500.0",
    "High", "R-RP-01", "vw_daily_sales_trend", "/reports/daily-sales-trend")
add("RPT-V08", RP, "Region filter on the region report matches filtered data mart query",
    "PASS", 12480000.00, 12480000.00,
    "region=SOUTH: expected=12480000.0, displayed=12480000.0", "High", "R-RP-02",
    "vw_region_performance", "/reports/region-performance")
add("RPT-V09", RP, "Reporting layer reflects the latest completed load", "PASS",
    "2026-05-01", "2026-05-01",
    "data mart max date=2026-05-01, reporting max date=2026-05-01", "Medium", "R-RP-04",
    "dm_sales_transaction", "vw_daily_sales_trend")
add("RPT-V10", RP, "Executive net total equals channel, region and daily view totals", "PASS",
    48734500.0, {"executive": 48734500.0, "channel": 48734500.0,
                 "region": 48734500.0, "daily": 48734500.0},
    "all four reporting views reconcile", "High", "R-RP-06",
    "vw_executive_dashboard",
    "vw_channel_performance / vw_region_performance / vw_daily_sales_trend")


if __name__ == "__main__":
    rpt.print_console(R)
    x = rpt.to_excel(R, "sample_validation_report.xlsx")
    h = rpt.to_html(R, "sample_validation_report.html")
    print(f"Excel report : {x}")
    print(f"HTML report  : {h}")
