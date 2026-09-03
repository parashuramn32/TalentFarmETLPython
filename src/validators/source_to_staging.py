"""Layer 1 validators - Source to Staging + master/reference data quality.

Schema notes (02CreateTables.sql):
  * status column is 'transaction_status' everywhere
  * retail_sales_raw and online_sales_raw already use 'transaction_id'
  * distributor_sales_raw.distributor_txn_id -> stg_distributor_sales.transaction_id
  * master/reference tables live in fs_source_retail
  * branch_region_mapping_raw is the region reference (no zone_name in this schema)
  * there is no distributor_master table
"""
import pandas as pd

from src.validators.base_validator import BaseValidator
from src.utils.config_loader import load_config, load_rules
from src.utils.result import ValidationResult
from src.utils.logger import get_logger

log = get_logger(__name__)
LAYER = "Source-to-Staging"
DQ = "Data Quality"


class SourceToStagingValidator(BaseValidator):
    def __init__(self, db, file_conn=None, api_conn=None):
        rules = load_rules()
        super().__init__(tolerance=rules["tolerance"]["amount"])
        self.db, self.file, self.api = db, file_conn, api_conn
        self.rules = rules
        self.cfg = load_config("source_config")

    # ==================== RETAIL ====================
    def run_retail(self, business_date, use_csv=False):
        cfg = self.cfg["retail"]
        tbl, sc = cfg["staging_table"], cfg["status_column"]
        results = []

        if use_csv and self.file is not None:
            src_all = self.file.read()
        else:
            src_all = self.db.query(cfg["source_db"],
                                    f"SELECT * FROM {cfg['source_table']} WHERE sale_date = :d",
                                    {"d": business_date})
        src_ok = src_all[src_all[sc].astype(str).str.upper() == "COMPLETED"] \
            if not src_all.empty else src_all
        stg = self.db.query("staging", f"SELECT * FROM {tbl} WHERE sale_date = :d",
                            {"d": business_date})

        results.append(self.validate_count(
            "RET-V01", LAYER, "Retail COMPLETED source count vs staging count",
            len(src_ok), len(stg), cfg["source_table"], tbl, "Critical", "R-SS-01"))

        cancelled = src_all[src_all[sc].astype(str).str.upper() == "CANCELLED"] \
            if not src_all.empty else pd.DataFrame()
        leaked = stg[stg["transaction_id"].isin(cancelled["transaction_id"])] \
            if not stg.empty and not cancelled.empty else pd.DataFrame()
        results.append(self.validate_empty(
            "RET-V02", LAYER, "CANCELLED retail transactions must not reach staging",
            leaked, cfg["source_table"], tbl, "Critical", "R-SS-03"))

        results.append(self.validate_duplicates(
            "RET-V03", LAYER, "Duplicate transaction_id in retail staging",
            stg, "transaction_id", tbl, "High", "R-SS-02"))

        results.append(self.validate_key_sets(
            "RET-V04", LAYER, "All COMPLETED retail transaction_ids present in staging",
            src_ok["transaction_id"].tolist() if not src_ok.empty else [],
            stg["transaction_id"].tolist() if not stg.empty else [],
            f"{cfg['source_table']}.transaction_id", f"{tbl}.transaction_id",
            "High", "R-SS-04"))

        results.append(self.validate_not_null(
            "RET-V05", LAYER, "Mandatory fields not null in retail staging",
            stg, cfg["mandatory_fields"], tbl, "High", "R-SS-05"))

        results.append(self._schema_check("RET-V06", src_all, cfg["expected_columns"],
                                          cfg["source_table"]))
        results.append(self._datatypes("RET-V07", src_ok, cfg["source_table"]))

        results.append(self.validate_field_match(
            "RET-V08", LAYER, "Retail field-level comparison source vs staging",
            src_ok, stg, "transaction_id", cfg["compare_columns"],
            cfg["source_table"], tbl, "High", "R-SS-09"))

        bad_dates = src_ok[src_ok["sale_date"].astype(str) != str(business_date)] \
            if not src_ok.empty else pd.DataFrame()
        results.append(self.validate_empty(
            "RET-V09", LAYER, "All retail sale_date values match the business date",
            bad_dates, cfg["source_table"], "", "Medium", "R-SS-10"))

        results.append(self._batch_tag("RET-V10", stg, tbl))
        return results

    # ==================== DISTRIBUTOR ====================
    def run_distributor(self, business_date):
        cfg = self.cfg["distributor"]
        tbl, sc = cfg["staging_table"], cfg["status_column"]
        pk_s, pk_t = cfg["primary_key_source"], cfg["primary_key_target"]
        results = []

        src = self.db.query(cfg["source_db"],
                            f"SELECT * FROM {cfg['source_table']} WHERE sale_date = :d",
                            {"d": business_date})
        src_ok = src[src[sc].astype(str).str.upper() == "APPROVED"] if not src.empty else src
        stg = self.db.query("staging", f"SELECT * FROM {tbl} WHERE sale_date = :d",
                            {"d": business_date})

        results.append(self.validate_count(
            "DST-V01", LAYER, "Distributor APPROVED count vs staging count",
            len(src_ok), len(stg), cfg["source_table"], tbl, "Critical", "R-SS-01"))

        excluded = src[src[sc].astype(str).str.upper().isin(
            [s.upper() for s in cfg["excluded_status"]])] if not src.empty else pd.DataFrame()
        leaked = stg[stg[pk_t].isin(excluded[pk_s])] \
            if not stg.empty and not excluded.empty else pd.DataFrame()
        results.append(self.validate_empty(
            "DST-V02", LAYER, "Non-APPROVED distributor transactions must not reach staging",
            leaked, cfg["source_table"], tbl, "Critical", "R-SS-03"))

        results.append(self.validate_key_sets(
            "DST-V03", LAYER, "distributor_txn_id -> transaction_id rename preserves values",
            src_ok[pk_s].tolist() if not src_ok.empty else [],
            stg[pk_t].tolist() if not stg.empty else [],
            f"{cfg['source_table']}.{pk_s}", f"{tbl}.{pk_t}", "High", "R-SS-04"))

        results.append(self._null_customer_flag(stg, tbl))
        results.append(self.validate_duplicates(
            "DST-V05", LAYER, "Duplicate transaction_id in distributor staging",
            stg, pk_t, tbl, "High", "R-SS-02"))

        src_cmp = src_ok.rename(columns={pk_s: pk_t}).copy() if not src_ok.empty else src_ok
        results.append(self.validate_field_match(
            "DST-V06", LAYER, "Distributor field-level comparison source vs staging",
            src_cmp, stg, pk_t, cfg["compare_columns"],
            cfg["source_table"], tbl, "High", "R-SS-09"))

        results.append(self._commission_rule(stg, tbl))

        rm = self.db.query("source_retail",
                           "SELECT DISTINCT region_name FROM branch_region_mapping_raw")
        results.append(self.validate_reference_integrity(
            "DST-V09", LAYER, "Distributor region_code resolves to a known region",
            stg["region_code"].dropna().tolist() if not stg.empty else [],
            rm["region_name"].tolist() if not rm.empty else [],
            f"{tbl}.region_code", "branch_region_mapping_raw.region_name", "High", "R-DQ-04"))

        results.append(self._batch_tag("DST-V10", stg, tbl))
        return results

    # ==================== ONLINE ====================
    def run_online(self, business_date, from_date=None, to_date=None):
        cfg = self.cfg["online"]
        tbl, sc = cfg["staging_table"], cfg["status_column"]
        from_date, to_date = from_date or business_date, to_date or business_date
        results = [self._api_health(), self._api_auth()]

        api_df, total_reported = pd.DataFrame(), None
        try:
            api_df, total_reported = self.api.fetch_all_pages(from_date, to_date)
        except Exception as exc:
            log.error("API fetch failed: %s", exc)
        results.append(self._pagination(api_df, total_reported))
        results.append(self._json_schema(api_df))

        src = self.db.query(cfg["source_db"],
                            f"SELECT * FROM {cfg['source_table']} WHERE sale_date = :d",
                            {"d": business_date})
        src_ok = src[src[sc].astype(str).str.upper() == "COMPLETED"] if not src.empty else src
        stg = self.db.query("staging", f"SELECT * FROM {tbl} WHERE sale_date = :d",
                            {"d": business_date})

        results.append(self.validate_count(
            "ONL-V05", LAYER, "Online COMPLETED source count vs staging count",
            len(src_ok), len(stg), cfg["source_table"], tbl, "Critical", "R-SS-01"))

        excluded = src[src[sc].astype(str).str.upper().isin(
            [s.upper() for s in cfg["excluded_status"]])] if not src.empty else pd.DataFrame()
        leaked = stg[stg["transaction_id"].isin(excluded["transaction_id"])] \
            if not stg.empty and not excluded.empty else pd.DataFrame()
        results.append(self.validate_empty(
            "ONL-V06", LAYER, "PENDING/FAILED online transactions must not reach staging",
            leaked, cfg["source_table"], tbl, "Critical", "R-SS-03"))

        results.append(self.validate_duplicates(
            "ONL-V07", LAYER, "Duplicate transaction_id in online staging / API pages",
            stg if not stg.empty else api_df, "transaction_id", tbl, "High", "R-SS-02"))

        results.append(self.validate_field_match(
            "ONL-V08", LAYER, "Online field-level comparison source vs staging",
            src_ok, stg, "transaction_id", cfg["compare_columns"],
            cfg["source_table"], tbl, "High", "R-SS-09"))

        results.append(self._date_range(api_df, from_date, to_date))
        return results

    # ==================== MASTER / REFERENCE ====================
    def run_master(self):
        results = []
        m = self.cfg["master"]
        for tc, spec, label in (("MST-V01", m["customer"], "Customer master"),
                                ("MST-V06", m["product"], "Product master"),
                                ("MST-V08", m["branch_region"], "Branch/region mapping")):
            src_n = self.db.count(spec["source_db"], spec["source_table"])
            stg_n = self.db.count("staging", spec["staging_table"])
            results.append(self.validate_count(
                tc, LAYER, f"{label} load completeness source vs staging",
                src_n, stg_n, spec["source_table"], spec["staging_table"], "High", "R-DQ-01"))

        cust = self.db.query("source_retail",
                             "SELECT customer_id, customer_name, dob, state, mobile, "
                             "email, kyc_status FROM customer_master_raw")
        results.append(self.validate_pattern(
            "MST-V02", DQ, "Customer mobile is a valid 10-digit Indian number",
            cust, "mobile", self.rules["patterns"]["mobile"],
            "customer_master_raw", "Medium", "R-DQ-02"))
        results.append(self._state_normalisation(cust))
        results.append(self._dob_plausibility(cust))

        prod = self.db.query("source_retail",
                             "SELECT product_code, standard_product_name, standard_product_type, "
                             "product_category, active_flag FROM product_master_raw")
        results.append(self.validate_domain(
            "MST-V07", DQ, "standard_product_type only INSURANCE or MUTUAL_FUND",
            prod, "standard_product_type",
            self.rules["allowed_values"]["standard_product_type"],
            "product_master_raw", "High", "R-DQ-08"))

        rm = self.db.query("source_retail",
                           "SELECT branch_code, branch_name, city, state, region_name, "
                           "active_flag FROM branch_region_mapping_raw")
        results.append(self.validate_domain(
            "MST-V09", DQ, "region_name within the allowed region domain",
            rm, "region_name", self.rules["allowed_values"]["region_name"],
            "branch_region_mapping_raw", "Medium", "R-DQ-09"))

        results.append(self._orphans("MST-V10", "product_code", prod["product_code"].tolist(),
                                     "product_master_raw", "R-DQ-04"))
        results.append(self._orphans("MST-V11", "customer_id", cust["customer_id"].tolist(),
                                     "customer_master_raw", "R-DQ-05"))
        results.append(self._branch_orphans(rm["branch_code"].tolist()))
        results.append(self._conditional_mandatory())
        return results

    # ==================== helpers ====================
    def _schema_check(self, tc, df, expected, obj):
        actual = list(df.columns)
        missing = [c for c in expected if c not in actual]
        extra = [c for c in actual if c not in expected]
        res = ValidationResult(
            test_case_id=tc, layer=LAYER,
            description="Retail source schema matches expected column list",
            source_object=obj, expected=f"{len(expected)} columns",
            actual=f"{len(actual)} columns", severity="Medium", risk_ref="R-SS-07")
        res.status = "PASS" if not missing else "FAIL"
        res.message = f"missing={missing or 'none'}, extra={extra or 'none'}"
        res.failed_sample = {"missing": missing, "extra": extra}
        log.info("[%s] %s - %s", tc, res.status, res.message)
        return res

    def _datatypes(self, tc, df, obj):
        issues = {}
        if not df.empty:
            dates = pd.to_datetime(df["sale_date"], errors="coerce")
            if dates.isna().any():
                issues["sale_date_invalid"] = int(dates.isna().sum())
            gross = pd.to_numeric(df["gross_amount"], errors="coerce")
            if gross.isna().any():
                issues["gross_not_numeric"] = int(gross.isna().sum())
            if (gross.dropna() <= 0).any():
                issues["gross_not_positive"] = int((gross.dropna() <= 0).sum())
            disc = pd.to_numeric(df["discount_amount"], errors="coerce")
            bad = ((disc.notna()) & (gross.notna()) & (disc >= gross)).sum()
            if bad:
                issues["discount_ge_gross"] = int(bad)
        res = ValidationResult(
            test_case_id=tc, layer=LAYER,
            description="Data types and numeric business rules on source",
            source_object=obj, expected="valid types, gross>0, discount<gross",
            actual=issues or "all valid", severity="Medium", risk_ref="R-SS-08")
        res.status = "PASS" if not issues else "FAIL"
        res.message = f"issues: {issues or 'none'}"
        log.info("[%s] %s - %s", tc, res.status, res.message)
        return res

    def _batch_tag(self, tc, stg, tbl):
        res = ValidationResult(
            test_case_id=tc, layer=LAYER,
            description="Every staging row carries load_batch_id and loaded_at",
            target_object=tbl, expected="no null load_batch_id / loaded_at",
            severity="Low", risk_ref="R-SS-11")
        if stg.empty:
            res.status, res.message = "SKIPPED", "No staging data"
            return res
        missing = [c for c in ("load_batch_id", "loaded_at") if c not in stg.columns]
        if missing:
            res.status, res.message = "FAIL", f"audit column(s) absent: {missing}"
            log.info("[%s] %s - %s", tc, res.status, res.message)
            return res
        n = int(stg["load_batch_id"].isna().sum() + stg["loaded_at"].isna().sum())
        res.actual = f"{n} null audit value(s)"
        res.status = "PASS" if n == 0 else "FAIL"
        res.message = f"{n} row(s) missing load lineage values"
        log.info("[%s] %s - %s", tc, res.status, res.message)
        return res

    def _null_customer_flag(self, stg, tbl):
        res = ValidationResult(
            test_case_id="DST-V04", layer=LAYER,
            description="Null customer_id in distributor staging is flagged for DM exclusion",
            target_object=tbl, expected="flagged, not propagated as valid",
            severity="High", risk_ref="R-SS-05")
        if stg.empty:
            res.status, res.message = "SKIPPED", "No staging data"
            return res
        n = int(stg["customer_id"].isna().sum())
        pct = round(100 * n / len(stg), 2) if len(stg) else 0
        res.actual = f"{n} null customer_id ({pct}%)"
        res.status = "PASS"
        res.message = f"{n} null customer_id row(s) flagged ({pct}%) - must be excluded from data mart"
        res.failed_sample = stg[stg["customer_id"].isna()]["transaction_id"].head(10).tolist()
        log.info("[DST-V04] %s - %s", res.status, res.message)
        return res

    def _commission_rule(self, stg, tbl):
        res = ValidationResult(
            test_case_id="DST-V08", layer=LAYER,
            description="commission_amount must not exceed gross_amount",
            target_object=tbl, expected="commission <= gross",
            severity="Medium", risk_ref="R-SS-13")
        if stg.empty or "commission_amount" not in stg.columns:
            res.status, res.message = "SKIPPED", "No commission data"
            return res
        c = pd.to_numeric(stg["commission_amount"], errors="coerce")
        g = pd.to_numeric(stg["gross_amount"], errors="coerce")
        bad = stg[(c.notna()) & (g.notna()) & (c > g)]
        res.actual = f"{len(bad)} breach(es)"
        res.status = "PASS" if bad.empty else "FAIL"
        res.message = f"{len(bad)} row(s) with commission > gross"
        res.failed_sample = bad["transaction_id"].head(10).tolist() if not bad.empty else []
        log.info("[DST-V08] %s - %s", res.status, res.message)
        return res

    def _api_health(self):
        res = ValidationResult(
            test_case_id="ONL-V01", layer=LAYER,
            description="Online Sales API health endpoint responds 200",
            source_object="/api/health", expected="HTTP 200",
            severity="High", risk_ref="R-SS-14")
        try:
            r = self.api.health()
            res.actual = f"HTTP {r.status_code}"
            res.status = "PASS" if r.status_code == 200 else "FAIL"
            res.message = f"health endpoint returned {r.status_code}"
        except Exception as exc:
            res.status, res.actual, res.message = "ERROR", "exception", str(exc)[:200]
        log.info("[ONL-V01] %s - %s", res.status, res.message)
        return res

    def _api_auth(self):
        res = ValidationResult(
            test_case_id="ONL-V02", layer=LAYER,
            description="API rejects an invalid X-API-Key with HTTP 401",
            source_object="/api/online-sales", expected="HTTP 401",
            severity="Medium", risk_ref="R-SS-15")
        try:
            r = self.api._get(self.api.cfg["endpoint"],
                              params={"from_date": "2026-05-01", "to_date": "2026-05-01"},
                              api_key_override="invalid-key-test")
            res.actual = f"HTTP {r.status_code}"
            res.status = "PASS" if r.status_code == 401 else "FAIL"
            res.message = f"invalid key returned {r.status_code} (expected 401)"
        except Exception as exc:
            res.status, res.actual, res.message = "ERROR", "exception", str(exc)[:200]
        log.info("[ONL-V02] %s - %s", res.status, res.message)
        return res

    def _pagination(self, df, total_reported):
        res = ValidationResult(
            test_case_id="ONL-V03", layer=LAYER,
            description="All API pages retrieved - collected rows equal total_records",
            source_object="/api/online-sales", expected=total_reported,
            actual=len(df), severity="Critical", risk_ref="R-SS-06")
        if total_reported is None:
            res.status, res.message = "SKIPPED", "total_records not present in envelope"
            return res
        res.status = "PASS" if len(df) == total_reported else "FAIL"
        res.message = f"collected={len(df)}, total_records={total_reported}"
        log.info("[ONL-V03] %s - %s", res.status, res.message)
        return res

    def _json_schema(self, df):
        expected = ["transaction_id", "sale_date", "customer_id", "product_code",
                    "product_name_raw", "product_type_raw", "gross_amount",
                    "discount_amount", "transaction_status", "region_code",
                    "payment_mode", "created_at"]
        res = ValidationResult(
            test_case_id="ONL-V04", layer=LAYER,
            description="API JSON response conforms to online_sales_raw schema",
            source_object="/api/online-sales", expected=f"{len(expected)} fields",
            severity="Medium", risk_ref="R-SS-16")
        if df is None or df.empty:
            res.status, res.message = "SKIPPED", "No API records returned"
            return res
        missing = [c for c in expected if c not in df.columns]
        res.actual = f"{len(df.columns)} fields, missing={missing or 'none'}"
        res.status = "PASS" if not missing else "FAIL"
        res.message = f"missing fields: {missing or 'none'}"
        log.info("[ONL-V04] %s - %s", res.status, res.message)
        return res

    def _date_range(self, df, from_date, to_date):
        res = ValidationResult(
            test_case_id="ONL-V09", layer=LAYER,
            description="All API records fall within the requested date range",
            source_object="/api/online-sales", expected=f"{from_date}..{to_date}",
            severity="Medium", risk_ref="R-SS-17")
        if df is None or df.empty or "sale_date" not in df.columns:
            res.status, res.message = "SKIPPED", "No API records"
            return res
        d = pd.to_datetime(df["sale_date"], errors="coerce")
        bad = df[(d < pd.to_datetime(from_date)) | (d > pd.to_datetime(to_date))]
        res.actual = f"{len(bad)} out-of-range record(s)"
        res.status = "PASS" if bad.empty else "FAIL"
        res.message = f"{len(bad)} record(s) outside requested range"
        log.info("[ONL-V09] %s - %s", res.status, res.message)
        return res

    def _state_normalisation(self, cust):
        res = ValidationResult(
            test_case_id="MST-V03", layer=DQ,
            description="Customer state values normalise to a known state",
            source_object="customer_master_raw.state",
            target_object="branch_region_mapping_raw.state",
            expected="every state variant resolves", severity="High", risk_ref="R-DQ-03")
        rm = self.db.query("source_retail",
                           "SELECT DISTINCT state FROM branch_region_mapping_raw")
        if cust.empty or rm.empty:
            res.status, res.message = "SKIPPED", "No customer or mapping data"
            return res
        known = {str(s).strip().upper() for s in rm["state"].dropna()}
        norm = {k.upper(): v.upper() for k, v in self.rules["state_normalisation"].items()}
        unresolved = sorted({str(s).strip() for s in cust["state"].dropna()
                             if str(s).strip().upper() not in known
                             and norm.get(str(s).strip().upper()) not in known})
        res.actual = f"{len(unresolved)} unresolved value(s)"
        res.failed_sample = unresolved[:10]
        res.status = "PASS" if not unresolved else "FAIL"
        res.message = f"unresolved state values: {unresolved[:10] or 'none'}"
        log.info("[MST-V03] %s - %s", res.status, res.message)
        return res

    def _dob_plausibility(self, cust):
        res = ValidationResult(
            test_case_id="MST-V05", layer=DQ,
            description="Date of birth is present and plausible",
            source_object="customer_master_raw.dob",
            expected="no future dates, age <= 100", severity="Low", risk_ref="R-DQ-07")
        if cust.empty:
            res.status, res.message = "SKIPPED", "No customer data"
            return res
        d = pd.to_datetime(cust["dob"], errors="coerce")
        now = pd.Timestamp.now()
        issues = {"null_or_invalid": int(d.isna().sum()),
                  "future_dob": int((d > now).sum()),
                  "age_over_100": int((d < now - pd.Timedelta(days=365.25 * 100)).sum())}
        issues = {k: v for k, v in issues.items() if v}
        res.actual = issues or "all plausible"
        res.status = "PASS" if not issues else "FAIL"
        res.message = f"dob issues: {issues or 'none'}"
        log.info("[MST-V05] %s - %s", res.status, res.message)
        return res

    def _staging_union(self, column):
        frames = []
        for t in ("stg_retail_sales", "stg_distributor_sales", "stg_online_sales"):
            try:
                frames.append(self.db.query("staging", f"SELECT {column} FROM {t}"))
            except Exception as exc:
                log.warning("Could not read %s.%s: %s", t, column, exc)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def _orphans(self, tc, column, parent_values, parent_obj, risk):
        child = self._staging_union(column)
        return self.validate_reference_integrity(
            tc, DQ, f"Every staging {column} exists in {parent_obj}",
            child[column].dropna().tolist() if not child.empty else [],
            parent_values, f"stg_* {column}", parent_obj, "High", risk)

    def _branch_orphans(self, parent_values):
        stg = self.db.query("staging", "SELECT branch_code FROM stg_retail_sales")
        return self.validate_reference_integrity(
            "MST-V13", DQ, "Every retail branch_code exists in branch/region mapping",
            stg["branch_code"].dropna().tolist() if not stg.empty else [],
            parent_values, "stg_retail_sales.branch_code",
            "branch_region_mapping_raw.branch_code", "High", "R-DQ-04")

    def _conditional_mandatory(self):
        res = ValidationResult(
            test_case_id="MST-V12", layer=DQ,
            description="policy_number for INSURANCE and folio_number for MUTUAL_FUND populated",
            source_object="stg_retail_sales / stg_distributor_sales",
            target_object="product_master_raw",
            expected="conditional mandatory fields populated",
            severity="Medium", risk_ref="R-DQ-10")
        try:
            breaches = {}
            prod = self.db.query("source_retail",
                                 "SELECT product_code, standard_product_type FROM product_master_raw")
            for tbl in ("stg_retail_sales", "stg_distributor_sales"):
                stg = self.db.query("staging",
                                    f"SELECT product_code, policy_number, folio_number FROM {tbl}")
                if stg.empty or prod.empty:
                    continue
                m = stg.merge(prod, on="product_code", how="left")
                n_ins = int(m[m["standard_product_type"].astype(str).str.upper() == "INSURANCE"]
                            ["policy_number"].isna().sum())
                n_mf = int(m[m["standard_product_type"].astype(str).str.upper() == "MUTUAL_FUND"]
                           ["folio_number"].isna().sum())
                if n_ins:
                    breaches[f"{tbl}.policy_number"] = n_ins
                if n_mf:
                    breaches[f"{tbl}.folio_number"] = n_mf
            res.actual = breaches or "all populated"
            res.status = "PASS" if not breaches else "FAIL"
            res.message = f"conditional mandatory breaches: {breaches or 'none'}"
        except Exception as exc:
            res.status, res.message = "ERROR", str(exc)[:200]
        log.info("[MST-V12] %s - %s", res.status, res.message)
        return res
