"""Unit tests for the reusable validation primitives (no DB/API required).

Column names mirror the real schema in 02CreateTables.sql.
"""
import sys
from pathlib import Path
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.validators.base_validator import BaseValidator


@pytest.fixture
def v():
    return BaseValidator(tolerance=0.01)


def test_count_match(v):
    assert v.validate_count("T1", "L", "counts equal", 100, 100).status == "PASS"


def test_count_mismatch(v):
    r = v.validate_count("T2", "L", "counts differ", 100, 98)
    assert r.status == "FAIL" and "difference=-2" in r.message


def test_duplicates_detected(v):
    df = pd.DataFrame({"transaction_id": ["A", "B", "B", "C"]})
    r = v.validate_duplicates("T3", "L", "dupes", df, "transaction_id")
    assert r.status == "FAIL" and "B" in r.failed_sample


def test_duplicates_clean(v):
    df = pd.DataFrame({"sales_transaction_id": ["RET_A", "DST_B", "ONL_C"]})
    assert v.validate_duplicates("T4", "L", "no dupes", df,
                                 "sales_transaction_id").status == "PASS"


def test_not_null_breach(v):
    df = pd.DataFrame({"customer_id": ["C1", None, "C3"], "gross_amount": [1, 2, 3]})
    r = v.validate_not_null("T5", "L", "nulls", df, ["customer_id", "gross_amount"])
    assert r.status == "FAIL" and "customer_id" in dict(r.failed_sample)


def test_not_null_missing_column(v):
    df = pd.DataFrame({"customer_id": ["C1"]})
    r = v.validate_not_null("T5b", "L", "nulls", df, ["customer_id", "policy_number"])
    assert r.status == "FAIL" and dict(r.failed_sample)["policy_number"] == "COLUMN MISSING"


def test_key_sets_missing(v):
    r = v.validate_key_sets("T6", "L", "keys", ["A", "B", "C"], ["A", "B"])
    assert r.status == "FAIL" and "C" in r.failed_sample["missing_in_target"]


def test_key_sets_equal(v):
    assert v.validate_key_sets("T7", "L", "keys", ["A", "B"], ["B", "A"]).status == "PASS"


def test_field_match_tolerance(v):
    src = pd.DataFrame({"transaction_id": ["A"], "gross_amount": [100.00]})
    tgt = pd.DataFrame({"transaction_id": ["A"], "gross_amount": [100.005]})
    assert v.validate_field_match("T8", "L", "amounts", src, tgt,
                                  "transaction_id", ["gross_amount"]).status == "PASS"


def test_field_match_mismatch(v):
    src = pd.DataFrame({"transaction_id": ["A"], "gross_amount": [100.0]})
    tgt = pd.DataFrame({"transaction_id": ["A"], "gross_amount": [150.0]})
    assert v.validate_field_match("T9", "L", "amounts", src, tgt,
                                  "transaction_id", ["gross_amount"]).status == "FAIL"


def test_domain_violation(v):
    df = pd.DataFrame({"standard_product_type": ["INSURANCE", "EQUITY"]})
    r = v.validate_domain("T10", "L", "domain", df, "standard_product_type",
                          ["INSURANCE", "MUTUAL_FUND"])
    assert r.status == "FAIL" and "EQUITY" in r.failed_sample


def test_domain_channel_valid(v):
    df = pd.DataFrame({"source_channel": ["Retail", "Distributor", "Online"]})
    assert v.validate_domain("T10b", "L", "channels", df, "source_channel",
                             ["Retail", "Distributor", "Online"]).status == "PASS"


def test_pattern_mobile(v):
    df = pd.DataFrame({"mobile": ["9876543210", "12345"]})
    r = v.validate_pattern("T11", "L", "mobile", df, "mobile", r"^[6-9][0-9]{9}$")
    assert r.status == "FAIL" and "12345" in r.failed_sample


def test_reference_integrity_orphans(v):
    r = v.validate_reference_integrity("T12", "L", "fk",
                                       ["INS_LIFE_001", "MF_UNKNOWN_999"],
                                       ["INS_LIFE_001", "MF_EQ_LARGE_001"])
    assert r.status == "FAIL" and "MF_UNKNOWN_999" in r.failed_sample


def test_numeric_within_tolerance(v):
    assert v.validate_numeric("T13", "L", "net", 1000.00, 1000.005).status == "PASS"


def test_numeric_outside_tolerance(v):
    assert v.validate_numeric("T14", "L", "net", 1000.00, 1001.00).status == "FAIL"


def test_validate_empty_pass(v):
    assert v.validate_empty("T15", "L", "no leaks", pd.DataFrame()).status == "PASS"


def test_validate_empty_fail(v):
    df = pd.DataFrame({"transaction_id": ["X1"]})
    assert v.validate_empty("T16", "L", "leak found", df).status == "FAIL"


def test_net_amount_rule_no_reversal():
    """Schema has no reversal_amount: net = gross - discount."""
    df = pd.DataFrame({"gross_sales_amount": [50000.0], "discount_amount": [1000.0],
                       "net_sales_amount": [49000.0]})
    expected = df["gross_sales_amount"] - df["discount_amount"]
    assert float(expected.iloc[0]) == 49000.0


def test_active_flag_tinyint_domain(v):
    """active_flag is TINYINT (1/0), not Y/N."""
    df = pd.DataFrame({"active_flag": [1, 0, 1]})
    assert v.validate_domain("T18", "L", "active flag", df,
                             "active_flag", [0, 1]).status == "PASS"
