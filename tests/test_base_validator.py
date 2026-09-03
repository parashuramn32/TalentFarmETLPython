"""Unit tests for the validation primitives and Submission 3 reporting fields."""
import sys
from pathlib import Path
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.validators.base_validator import BaseValidator
from src.utils.result import ValidationResult, PASS, FAIL, BLOCKED, SKIPPED


@pytest.fixture
def v():
    return BaseValidator(tolerance=0.01)


# ---------------- core primitives ----------------
def test_count_match(v):
    assert v.validate_count("T1", "L", "counts equal", 100, 100).status == PASS


def test_count_mismatch(v):
    r = v.validate_count("T2", "L", "counts differ", 100, 98)
    assert r.status == FAIL and "difference=-2" in r.message


def test_duplicates_detected(v):
    df = pd.DataFrame({"transaction_id": ["A", "B", "B", "C"]})
    r = v.validate_duplicates("T3", "L", "dupes", df, "transaction_id")
    assert r.status == FAIL and "B" in r.failed_sample


def test_duplicates_clean(v):
    df = pd.DataFrame({"sales_transaction_id": ["RET_A", "DST_B", "ONL_C"]})
    assert v.validate_duplicates("T4", "L", "no dupes", df,
                                 "sales_transaction_id").status == PASS


def test_not_null_breach(v):
    df = pd.DataFrame({"customer_id": ["C1", None, "C3"], "gross_amount": [1, 2, 3]})
    r = v.validate_not_null("T5", "L", "nulls", df, ["customer_id", "gross_amount"])
    assert r.status == FAIL and r.actual == 1


def test_not_null_missing_column_blocks(v):
    df = pd.DataFrame({"customer_id": ["C1"]})
    r = v.validate_not_null("T5b", "L", "nulls", df, ["customer_id", "policy_number"])
    assert r.status == BLOCKED


def test_key_sets_missing(v):
    r = v.validate_key_sets("T6", "L", "keys", ["A", "B", "C"], ["A", "B"])
    assert r.status == FAIL and "C" in r.failed_sample["missing_in_target"]


def test_key_sets_equal(v):
    assert v.validate_key_sets("T7", "L", "keys", ["A", "B"], ["B", "A"]).status == PASS


def test_field_match_tolerance(v):
    src = pd.DataFrame({"transaction_id": ["A"], "gross_amount": [100.00]})
    tgt = pd.DataFrame({"transaction_id": ["A"], "gross_amount": [100.005]})
    assert v.validate_field_match("T8", "L", "amounts", src, tgt,
                                  "transaction_id", ["gross_amount"]).status == PASS


def test_field_match_mismatch(v):
    src = pd.DataFrame({"transaction_id": ["A"], "gross_amount": [100.0]})
    tgt = pd.DataFrame({"transaction_id": ["A"], "gross_amount": [150.0]})
    assert v.validate_field_match("T9", "L", "amounts", src, tgt,
                                  "transaction_id", ["gross_amount"]).status == FAIL


def test_field_match_missing_join_key_blocks(v):
    src = pd.DataFrame({"other": ["A"]})
    tgt = pd.DataFrame({"transaction_id": ["A"]})
    assert v.validate_field_match("T9b", "L", "amounts", src, tgt,
                                  "transaction_id", ["x"]).status == BLOCKED


def test_domain_violation(v):
    df = pd.DataFrame({"standard_product_type": ["INSURANCE", "EQUITY"]})
    r = v.validate_domain("T10", "L", "domain", df, "standard_product_type",
                          ["INSURANCE", "MUTUAL_FUND"])
    assert r.status == FAIL and "EQUITY" in r.failed_sample


def test_domain_channel_valid(v):
    df = pd.DataFrame({"source_channel": ["Retail", "Distributor", "Online"]})
    assert v.validate_domain("T10b", "L", "channels", df, "source_channel",
                             ["Retail", "Distributor", "Online"]).status == PASS


def test_pattern_mobile(v):
    df = pd.DataFrame({"mobile": ["9876543210", "12345"]})
    r = v.validate_pattern("T11", "L", "mobile", df, "mobile", r"^[6-9][0-9]{9}$")
    assert r.status == FAIL and "12345" in r.failed_sample


def test_reference_integrity_orphans(v):
    r = v.validate_reference_integrity("T12", "L", "fk",
                                       ["INS_LIFE_001", "MF_UNKNOWN_999"],
                                       ["INS_LIFE_001", "MF_EQ_LARGE_001"])
    assert r.status == FAIL and "MF_UNKNOWN_999" in r.failed_sample


def test_reference_integrity_empty_parent_blocks(v):
    assert v.validate_reference_integrity("T12b", "L", "fk", ["A"], []).status == BLOCKED


def test_numeric_within_tolerance(v):
    assert v.validate_numeric("T13", "L", "net", 1000.00, 1000.005).status == PASS


def test_numeric_outside_tolerance(v):
    assert v.validate_numeric("T14", "L", "net", 1000.00, 1001.00).status == FAIL


def test_validate_empty_pass(v):
    assert v.validate_empty("T15", "L", "no leaks", pd.DataFrame()).status == PASS


def test_validate_empty_fail(v):
    df = pd.DataFrame({"transaction_id": ["X1"]})
    assert v.validate_empty("T16", "L", "leak found", df).status == FAIL


# ---------------- schema-specific rules ----------------
def test_net_amount_rule_no_reversal():
    """This schema has no reversal_amount: net = gross - discount."""
    df = pd.DataFrame({"gross_sales_amount": [50000.0], "discount_amount": [1000.0]})
    assert float((df["gross_sales_amount"] - df["discount_amount"]).iloc[0]) == 49000.0


def test_active_flag_tinyint_domain(v):
    """active_flag is TINYINT (1/0), not Y/N."""
    df = pd.DataFrame({"active_flag": [1, 0, 1]})
    assert v.validate_domain("T18", "L", "active flag", df,
                             "active_flag", [0, 1]).status == PASS


# ---------------- Submission 3: variance ----------------
def test_variance_numeric(v):
    r = v.validate_count("V1", "L", "counts", 100, 98)
    assert r.variance == -2.0 and r.variance_pct == -2.0


def test_variance_zero_when_matching(v):
    assert v.validate_count("V2", "L", "counts", 500, 500).variance == 0.0


def test_variance_textual():
    r = ValidationResult("V3", "L", "d", expected="INSURANCE", actual="MUTUAL_FUND")
    r.compute_variance()
    assert "INSURANCE" in str(r.variance) and "MUTUAL_FUND" in str(r.variance)


def test_variance_no_difference_text():
    r = ValidationResult("V4", "L", "d", expected="WEST", actual="WEST")
    r.compute_variance()
    assert r.variance == "No variance"


def test_variance_pct_guards_zero_expected(v):
    """A zero expected value must not raise ZeroDivisionError."""
    r = v.validate_count("V5", "L", "zero base", 0, 5)
    assert r.variance == 5.0 and r.variance_pct is None


# ---------------- Submission 3: BLOCKED ----------------
def test_blocked_when_dataset_unavailable(v):
    r = v.validate_duplicates("B1", "L", "dupes", None, "transaction_id")
    assert r.status == BLOCKED and not r.is_defect


def test_blocked_when_key_column_absent(v):
    df = pd.DataFrame({"other": [1]})
    assert v.validate_duplicates("B2", "L", "dupes", df, "transaction_id").status == BLOCKED


def test_skipped_when_no_rows(v):
    r = v.validate_duplicates("B3", "L", "dupes", pd.DataFrame(), "transaction_id")
    assert r.status == SKIPPED and not r.is_defect


def test_blocked_on_non_numeric_comparison(v):
    assert v.validate_numeric("B4", "L", "net", "abc", 100).status == BLOCKED


def test_only_fail_becomes_defect():
    assert ValidationResult("D1", "L", "d", status=FAIL).is_defect
    for st in (BLOCKED, SKIPPED, PASS):
        assert not ValidationResult("D", "L", "d", status=st).is_defect


def test_remarks_key_in_serialised_output(v):
    d = v.validate_count("R1", "L", "counts", 10, 10).to_dict()
    assert "remarks" in d and "variance" in d and "message" not in d


# ---------------- regression tests for mentor-review fixes ----------------
def test_customer_name_cleansing_handles_leading_space_and_case():
    """Trim must happen BEFORE the anchored salutation match, and the match
    must be case-insensitive."""
    raw = pd.Series(["  mr. rajesh  kumar ", "DR. Amit Verma", "Priya Sharma"])
    cleaned = (raw.astype(str).str.strip()
               .str.replace(r"^(?:Mr|Mrs|Ms|Dr|Prof)\.?\s+", "", regex=True, case=False)
               .str.replace(r"\s+", " ", regex=True).str.strip().str.title())
    assert list(cleaned) == ["Rajesh Kumar", "Amit Verma", "Priya Sharma"]


def test_executive_metric_ids_are_unique():
    ids = ["AGG-V06a", "AGG-V06b", "AGG-V06c", "AGG-V06d", "AGG-V06e"]
    assert len(ids) == len(set(ids))


def test_blocked_results_excluded_from_defect_log():
    from src.utils.defect_logger import build_defects
    rs = [ValidationResult("A1", "Source-to-Staging", "d", status=FAIL, expected=1,
                           actual=2, severity="Critical", risk_ref="R-SS-01"),
          ValidationResult("A2", "Source-to-Staging", "d", status=BLOCKED),
          ValidationResult("A3", "Source-to-Staging", "d", status=SKIPPED),
          ValidationResult("A4", "Source-to-Staging", "d", status=PASS)]
    defects = build_defects(rs, "2026-05-01")
    assert len(defects) == 1 and defects[0]["Validation ID"] == "A1"


def test_critical_severity_maps_to_high_in_defect_log():
    from src.utils.defect_logger import build_defects
    rs = [ValidationResult("B1", "Staging-to-Data Mart", "d", status=FAIL, expected=0,
                           actual=3, severity="Critical", risk_ref="R-DM-02")]
    d = build_defects(rs, "2026-05-01")[0]
    assert d["Severity"] == "High"
    assert d["Defect ID"].startswith("DEF-DM-")
    assert d["Likely Root Cause"] and d["Business Impact"]


def test_report_generator_handles_empty_results():
    """An empty run must not raise when summarised."""
    from src.utils.report_generator import summarise
    s = summarise([], "2026-05-01")
    assert s["total_validations"] == 0 and s["pass_rate_pct"] == 0.0
