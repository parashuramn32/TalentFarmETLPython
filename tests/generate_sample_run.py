"""
Demonstrates the Submission 3 reporting format end-to-end without live
lab connectivity. Produces a validation report, defect log and evidence files.

In the lab, the real command is:  python -m src.main --date 2026-05-01
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.validators.base_validator import BaseValidator
from src.utils.result import ValidationResult, PASS, FAIL, BLOCKED, SKIPPED
from src.utils import report_generator as rpt
from src.utils import defect_logger

V = BaseValidator(tolerance=0.01)
R = []
SS, DQ, DM, AG, RP = ("Source-to-Staging", "Data Quality", "Staging-to-Data Mart",
                      "Aggregate Model", "Data Mart-to-Reporting")
BUSINESS_DATE = "2026-05-01"


def add(tc, layer, desc, status, expected, actual, msg, sev, risk,
        src="", tgt="", sample=None):
    r = ValidationResult(test_case_id=tc, layer=layer, description=desc,
                         source_object=src, target_object=tgt,
                         expected=expected, actual=actual, severity=sev, risk_ref=risk,
                         environment="LAB-GCP-MySQL")
    r.status, r.message, r.failed_sample = status, msg, sample
    r.compute_variance()
    R.append(r)


# ---- Source-to-Staging ----
R.append(V.validate_count("RET-V01", SS, "Retail COMPLETED source count vs staging count",
                          842, 843, "retail_sales_raw", "stg_retail_sales",
                          "Critical", "R-SS-01"))
add("RET-V02", SS, "CANCELLED retail transactions must not reach staging", FAIL, 0, 1,
    "1 offending row(s) found", "Critical", "R-SS-03",
    "retail_sales_raw", "stg_retail_sales", [{"transaction_id": "RTL20260501007"}])
add("RET-V03", SS, "Duplicate transaction_id in retail staging", PASS, 0, 0,
    "0 duplicate value(s) for key 'transaction_id'", "High", "R-SS-02", "", "stg_retail_sales")
add("RET-V05", SS, "Mandatory fields not null in retail staging", PASS, 0, 0,
    "null/blank breaches: none", "High", "R-SS-05", "", "stg_retail_sales")
add("DST-V01", SS, "Distributor APPROVED count vs staging count", PASS, 412, 412,
    "source=412, target=412, difference=0", "Critical", "R-SS-01",
    "distributor_sales_raw", "stg_distributor_sales")
add("DST-V09", SS, "Distributor region_code resolves to a known region", FAIL, 0, 1,
    "1 orphan reference(s)", "High", "R-DQ-04",
    "stg_distributor_sales.region_code", "branch_region_mapping_raw.region_name", ["NORTHEAST"])
add("ONL-V01", SS, "Online Sales API health endpoint responds 200", PASS, 200, 200,
    "health endpoint returned HTTP 200", "High", "R-SS-14", "/api/health")
add("ONL-V03", SS, "All API pages retrieved - collected rows equal total_records",
    PASS, 734, 734, "collected=734, total_records=734", "Critical", "R-SS-06",
    "/api/online-sales")
add("ONL-V05", SS, "Online COMPLETED source count vs staging count", PASS, 689, 689,
    "source=689, target=689, difference=0", "Critical", "R-SS-01",
    "online_sales_raw", "stg_online_sales")

# ---- Data Quality ----
add("MST-V02", DQ, "Customer mobile is a valid 10-digit Indian number", FAIL, 0, 2,
    "2 value(s) failed pattern ^[6-9][0-9]{9}$", "Medium", "R-DQ-02",
    "customer_master_raw", "", ["+919812345678", "98765"])
add("MST-V03", DQ, "Customer state values normalise to a known state", FAIL, 0, 3,
    "unresolved state values: ['Mahrashtra', 'Karnatka', 'TN ']", "High", "R-DQ-03",
    "customer_master_raw.state", "branch_region_mapping_raw.state",
    ["Mahrashtra", "Karnatka", "TN "])
add("MST-V04", DQ, "REJECTED-KYC customers must not contribute to net sales", FAIL, 0, 2,
    "2 REJECTED-KYC transaction(s) contributing 84000.0 to net sales", "Critical", "R-DQ-06",
    "customer_master_raw.kyc_status", "dm_sales_transaction",
    [{"sales_transaction_id": "DST_20260501118", "customer_id": "CUST0455"}])
add("MST-V07", DQ, "standard_product_type only INSURANCE or MUTUAL_FUND", PASS, 0, 0,
    "values outside allowed domain: none", "High", "R-DQ-08", "product_master_raw")
add("MST-V13", DQ, "Every retail branch_code exists in branch/region mapping", FAIL, 0, 1,
    "1 orphan reference(s)", "High", "R-DQ-04",
    "stg_retail_sales.branch_code", "branch_region_mapping_raw.branch_code", ["BR_NEW_099"])

# ---- Staging to Data Mart ----
add("DM-V01", DM, "standard_product_type in data mart matches product master", FAIL, 0, 1,
    "1 transaction(s) with an incorrect standard_product_type", "Critical", "R-DM-01",
    "product_master_raw", "dm_sales_transaction",
    [{"sales_transaction_id": "RET_RTL20260501003", "product_code": "INS_ULIP_004",
      "standard_product_type_dm": "MUTUAL_FUND", "standard_product_type_pm": "INSURANCE"}])
add("DM-V02", DM, "ULIP products must map to INSURANCE (not MUTUAL_FUND)", FAIL, 0, 1,
    "1 of 1 ULIP transaction(s) not classified as INSURANCE", "Critical", "R-DM-02",
    "stg_* product_type_raw", "dm_sales_transaction", ["RET_RTL20260501003"])
add("DM-V05", DM, "source_channel holds standardised values", PASS, 0, 0,
    "0 row(s) with a non-standard source_channel", "High", "R-DM-04",
    "stg_*", "dm_sales_transaction.source_channel")
add("DM-V08", DM, "Online region derived from normalised customer_state", FAIL, 0, 8,
    "8 of 689 online transaction(s) with incorrect region", "High", "R-DM-06",
    "customer_master_raw.state", "dm_sales_transaction.region_name",
    [{"sales_transaction_id": "ONL_20260501042", "customer_state": "Mahrashtra",
      "region_name": "UNKNOWN_REGION", "_exp": "WEST"}])
R.append(V.validate_numeric("DM-V12", DM,
                            "net_sales_amount = gross_sales_amount - discount_amount",
                            41060500.00, 41060500.00, "stg_* gross/discount",
                            "dm_sales_transaction.net_sales_amount", "Critical", "R-DM-10"))
add("DM-V13", DM, "No sales against inactive products (active_flag = 0)", FAIL, 0, 3,
    "3 transaction(s) reference discontinued products", "High", "R-DM-11",
    "product_master_raw.active_flag", "dm_sales_transaction",
    [{"sales_transaction_id": "DST_20260501067", "product_code": "MF_EQ_OLD_014"}])
add("DM-V14", DM, "Valid staging total vs data mart transaction count", PASS, 1108, 1108,
    "source=1108, target=1108, difference=0", "Critical", "R-DM-12",
    "stg_* (all channels)", "dm_sales_transaction")
add("DM-V15", DM, "Null-customer transactions must not count as valid net sales", FAIL, 0, 3,
    "3 null-customer transaction(s) contributing to net sales", "High", "R-DM-13",
    "stg_distributor_sales", "dm_sales_transaction", ["DST_20260501201"])

# ---- Aggregates ----
add("AGG-V01", AG, "Region summary sums and counts vs transaction level", PASS, 0, 0,
    "aggregate breaches: none", "Critical", "R-AG-01",
    "dm_sales_transaction", "dm_sales_region_summary")
add("AGG-V04", AG, "Product summary grouping and totals vs transaction level", FAIL, 0, 2,
    "aggregate breaches: {'total_net_sales_amount': 2}", "High", "R-AG-02",
    "dm_sales_transaction", "dm_sales_product_summary")
R.append(V.validate_numeric("AGG-V06", AG,
                            "Executive metric 'total_insurance_premium' matches recomputed value",
                            21980000.00, 22100000.00, "dm_sales_transaction",
                            "dm_executive_sales_summary", "Critical", "R-AG-04"))
add("AGG-V08", AG, "All summary models reconcile to the same daily net total", FAIL,
    48734500.0, 48612000.0, "summary totals: product model differs by 122500.0",
    "High", "R-AG-06", "dm_sales_transaction", "dm_* summary models")

# ---- Reporting ----
add("RPT-V01", RP, "vw_executive_dashboard reconciles with dm_executive_sales_summary",
    PASS, 48734500.00, 48734500.00, "data mart: 1 rows / 48734500.0 | view: 1 rows / 48734500.0",
    "Critical", "R-RP-05", "dm_executive_sales_summary", "vw_executive_dashboard")
add("RPT-V05", RP, "/reports/region-performance displayed total vs vw_region_performance",
    FAIL, 48734500.00, 48348000.00, "view=48734500.0, displayed=48348000.0",
    "High", "R-RP-01", "vw_region_performance", "/reports/region-performance")
add("RPT-V09", RP, "Reporting layer reflects the latest completed load", PASS,
    "2026-05-01", "2026-05-01",
    "data mart max date=2026-05-01, reporting max date=2026-05-01", "Medium", "R-RP-04",
    "dm_sales_transaction", "vw_daily_sales_trend")

# ---- Blocked / skipped demonstrations ----
r = ValidationResult("RPT-V12", RP, "vw_product_performance reconciles with dm_sales_product_summary",
                     source_object="dm_sales_product_summary", target_object="vw_product_performance",
                     severity="High", risk_ref="R-RP-05", environment="LAB-GCP-MySQL")
r.mark_blocked("Report page unreachable: ConnectionError")
R.append(r)

r2 = ValidationResult("ONL-V04", SS, "API JSON response conforms to online_sales_raw schema",
                      source_object="/api/online-sales", severity="Medium",
                      risk_ref="R-SS-16", environment="LAB-GCP-MySQL")
r2.mark_skipped("No API records returned for the requested range")
R.append(r2)


if __name__ == "__main__":
    rpt.print_console(R, BUSINESS_DATE)
    x = rpt.to_excel(R, "sample_validation_report.xlsx", BUSINESS_DATE)
    h = rpt.to_html(R, "sample_validation_report.html", BUSINESS_DATE)
    d, defects = defect_logger.to_excel(R, "sample_defect_log.xlsx", BUSINESS_DATE)
    print(f"Validation report (Excel) : {x}")
    print(f"Validation report (HTML)  : {h}")
    print(f"Defect log                : {d}  ({len(defects)} defects)")
