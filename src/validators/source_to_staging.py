"""Layer 1 - Source to Staging, plus master/reference data quality.

Schema notes (02CreateTables.sql):
  * the status column is 'transaction_status' everywhere
  * retail_sales_raw and online_sales_raw already use 'transaction_id'
  * only distributor_sales_raw.distributor_txn_id is renamed on load
  * master/reference tables live in fs_source_retail
  * branch_region_mapping_raw is the region reference (no zone_name)
  * there is no distributor_master table
"""
import pandas as pd

from src.validators.base_validator import BaseValidator
from src.utils.config_loader import load_config, load_rules
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

    def _q(self, alias, sql, params=None):
        """Query returning None on failure, so callers can mark BLOCKED."""
        try:
            return self.db.query(alias, sql, params)
        except Exception as exc:
            log.error("Query failed on %s: %s", alias, exc)
            return None

    @staticmethod
    def _filter_status(df, col, values):
        if df is None or df.empty or col not in df.columns:
            return pd.DataFrame()
        wanted = {v.upper() for v in values}
        return df[df[col].astype(str).str.upper().isin(wanted)]

    # ==================== RETAIL ====================
    def run_retail(self, business_date, use_csv=False):
        cfg = self.cfg["retail"]
        tbl, sc, pk = cfg["staging_table"], cfg["status_column"], cfg["primary_key"]
        results = []

        if use_csv and self.file is not None:
            try:
                src_all = self.file.read()
                if "sale_date" in src_all.columns:
                    src_all = src_all[src_all["sale_date"].astype(str) == str(business_date)]
            except Exception as exc:
                log.error("Retail CSV read failed: %s", exc)
                src_all = None
        else:
            src_all = self._q(cfg["source_db"],
                              f"SELECT * FROM {cfg['source_table']} WHERE sale_date = :d",
                              {"d": business_date})
        stg = self._q("staging", f"SELECT * FROM {tbl} WHERE sale_date = :d",
                      {"d": business_date})
        blocked = src_all is None or stg is None
        src_ok = self._filter_status(src_all, sc, cfg["status_filter"])

        if blocked:
            results.append(self._blocked(
                self._res("RET-V01", LAYER, "Retail COMPLETED source count vs staging count",
                          source_object=cfg["source_table"], target_object=tbl,
                          severity="Critical", risk_ref="R-SS-01"),
                "Source or staging table could not be read"))
        else:
            results.append(self.validate_count(
                "RET-V01", LAYER, "Retail COMPLETED source count vs staging count",
                len(src_ok), len(stg), cfg["source_table"], tbl, "Critical", "R-SS-01"))

        excluded = self._filter_status(src_all, sc, cfg["excluded_status"])
        leaked = None if blocked else (
            stg[stg[pk].isin(excluded[pk])] if not excluded.empty else pd.DataFrame())
        results.append(self.validate_empty(
            "RET-V02", LAYER, "CANCELLED retail transactions must not reach staging",
            leaked, cfg["source_table"], tbl, "Critical", "R-SS-03"))

        results.append(self.validate_duplicates(
            "RET-V03", LAYER, "Duplicate transaction_id in retail staging",
            stg, pk, tbl, "High", "R-SS-02"))

        if blocked:
            results.append(self._blocked(
                self._res("RET-V04", LAYER,
                          "All COMPLETED retail transaction_ids present in staging",
                          source_object=f"{cfg['source_table']}.{pk}",
                          target_object=f"{tbl}.{pk}", severity="High", risk_ref="R-SS-04"),
                "Source or staging table could not be read"))
        else:
            results.append(self.validate_key_sets(
                "RET-V04", LAYER, "All COMPLETED retail transaction_ids present in staging",
                src_ok[pk].tolist(), stg[pk].tolist(),
                f"{cfg['source_table']}.{pk}", f"{tbl}.{pk}", "High", "R-SS-04"))

        results.append(self.validate_not_null(
            "RET-V05", LAYER, "Mandatory fields not null in retail staging",
            stg, cfg["mandatory_fields"], tbl, "High", "R-SS-05"))
        results.append(self._schema_check("RET-V06", src_all, cfg["expected_columns"],
                                          cfg["source_table"]))
        results.append(self._datatypes("RET-V07", None if blocked else src_ok,
                                       cfg["source_table"]))
        results.append(self.validate_field_match(
            "RET-V08", LAYER, "Retail field-level comparison source vs staging",
            None if blocked else src_ok, stg, pk, cfg["compare_columns"],
            cfg["source_table"], tbl, "High", "R-SS-09"))

        bad_dates = None if blocked else src_ok[
            src_ok["sale_date"].astype(str) != str(business_date)]
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

        src = self._q(cfg["source_db"],
                      f"SELECT * FROM {cfg['source_table']} WHERE sale_date = :d",
                      {"d": business_date})
        stg = self._q("staging", f"SELECT * FROM {tbl} WHERE sale_date = :d",
                      {"d": business_date})
        blocked = src is None or stg is None
        src_ok = self._filter_status(src, sc, cfg["status_filter"])

        if blocked:
            results.append(self._blocked(
                self._res("DST-V01", LAYER, "Distributor APPROVED count vs staging count",
                          source_object=cfg["source_table"], target_object=tbl,
                          severity="Critical", risk_ref="R-SS-01"),
                "Source or staging table could not be read"))
        else:
            results.append(self.validate_count(
                "DST-V01", LAYER, "Distributor APPROVED count vs staging count",
                len(src_ok), len(stg), cfg["source_table"], tbl, "Critical", "R-SS-01"))

        excluded = self._filter_status(src, sc, cfg["excluded_status"])
        leaked = None if blocked else (
            stg[stg[pk_t].isin(excluded[pk_s])] if not excluded.empty else pd.DataFrame())
        results.append(self.validate_empty(
            "DST-V02", LAYER, "Non-APPROVED distributor transactions must not reach staging",
            leaked, cfg["source_table"], tbl, "Critical", "R-SS-03"))

        if blocked:
            results.append(self._blocked(
                self._res("DST-V03", LAYER,
                          "distributor_txn_id to transaction_id rename preserves values",
                          source_object=f"{cfg['source_table']}.{pk_s}",
                          target_object=f"{tbl}.{pk_t}", severity="High", risk_ref="R-SS-04"),
                "Source or staging table could not be read"))
        else:
            results.append(self.validate_key_sets(
                "DST-V03", LAYER, "distributor_txn_id to transaction_id rename preserves values",
                src_ok[pk_s].tolist(), stg[pk_t].tolist(),
                f"{cfg['source_table']}.{pk_s}", f"{tbl}.{pk_t}", "High", "R-SS-04"))

        results.append(self._null_customer_flag(stg, tbl))
        results.append(self.validate_duplicates(
            "DST-V05", LAYER, "Duplicate transaction_id in distributor staging",
            stg, pk_t, tbl, "High", "R-SS-02"))

        src_cmp = None if blocked or src_ok.empty else src_ok.rename(columns={pk_s: pk_t})
        results.append(self.validate_field_match(
            "DST-V06", LAYER, "Distributor field-level comparison source vs staging",
            src_cmp, stg, pk_t, cfg["compare_columns"],
            cfg["source_table"], tbl, "High", "R-SS-09"))
        results.append(self._commission_rule(stg, tbl))

        rm = self._q("source_retail",
                     "SELECT DISTINCT region_name FROM branch_region_mapping_raw")
        results.append(self.validate_reference_integrity(
            "DST-V09", LAYER, "Distributor region_code resolves to a known region",
            [] if stg is None else stg["region_code"].dropna().tolist(),
            [] if rm is None else rm["region_name"].tolist(),
            f"{tbl}.region_code", "branch_region_mapping_raw.region_name", "High", "R-DQ-04"))
        results.append(self._batch_tag("DST-V10", stg, tbl))
        return results

    # ==================== ONLINE ====================
    def run_online(self, business_date, from_date=None, to_date=None):
        cfg = self.cfg["online"]
        tbl, sc, pk = cfg["staging_table"], cfg["status_column"], cfg["primary_key"]
        from_date, to_date = from_date or business_date, to_date or business_date
        results = [self._api_health(), self._api_auth()]

        api_df, total_reported, api_failed = pd.DataFrame(), None, False
        try:
            api_df, total_reported = self.api.fetch_all_pages(from_date, to_date)
        except Exception as exc:
            log.error("API fetch failed: %s", exc)
            api_failed = True
        results.append(self._pagination(api_df, total_reported, api_failed))
        results.append(self._json_schema(api_df, api_failed))

        src = self._q(cfg["source_db"],
                      f"SELECT * FROM {cfg['source_table']} WHERE sale_date = :d",
                      {"d": business_date})
        stg = self._q("staging", f"SELECT * FROM {tbl} WHERE sale_date = :d",
                      {"d": business_date})
        blocked = src is None or stg is None
        src_ok = self._filter_status(src, sc, cfg["status_filter"])

        if blocked:
            results.append(self._blocked(
                self._res("ONL-V05", LAYER, "Online COMPLETED source count vs staging count",
                          source_object=cfg["source_table"], target_object=tbl,
                          severity="Critical", risk_ref="R-SS-01"),
                "Source or staging table could not be read"))
        else:
            results.append(self.validate_count(
                "ONL-V05", LAYER, "Online COMPLETED source count vs staging count",
                len(src_ok), len(stg), cfg["source_table"], tbl, "Critical", "R-SS-01"))

        excluded = self._filter_status(src, sc, cfg["excluded_status"])
        leaked = None if blocked else (
            stg[stg[pk].isin(excluded[pk])] if not excluded.empty else pd.DataFrame())
        results.append(self.validate_empty(
            "ONL-V06", LAYER, "PENDING or FAILED online transactions must not reach staging",
            leaked, cfg["source_table"], tbl, "Critical", "R-SS-03"))

        results.append(self.validate_duplicates(
            "ONL-V07", LAYER, "Duplicate transaction_id in online staging",
            stg, pk, tbl, "High", "R-SS-02"))
        results.append(self.validate_field_match(
            "ONL-V08", LAYER, "Online field-level comparison source vs staging",
            None if blocked else src_ok, stg, pk, cfg["compare_columns"],
            cfg["source_table"], tbl, "High", "R-SS-09"))
        results.append(self._date_range(api_df, from_date, to_date, api_failed))
        return results

    # ==================== MASTER / REFERENCE ====================
    def run_master(self, business_date=None):
        """Master load completeness and data quality.

        `business_date` scopes the transactional side of the orphan checks so
        the suite never scans whole staging tables on production volumes.
        """
        results = []
        m = self.cfg["master"]
        for tc, spec, label in (("MST-V01", m["customer"], "Customer master"),
                                ("MST-V06", m["product"], "Product master"),
                                ("MST-V08", m["branch_region"], "Branch/region mapping")):
            try:
                src_n = self.db.count(spec["source_db"], spec["source_table"])
                stg_n = self.db.count("staging", spec["staging_table"])
                results.append(self.validate_count(
                    tc, LAYER, f"{label} load completeness source vs staging",
                    src_n, stg_n, spec["source_table"], spec["staging_table"],
                    "High", "R-DQ-01"))
            except Exception as exc:
                results.append(self._blocked(
                    self._res(tc, LAYER, f"{label} load completeness source vs staging",
                              source_object=spec["source_table"],
                              target_object=spec["staging_table"],
                              severity="High", risk_ref="R-DQ-01"),
                    f"Count failed: {type(exc).__name__}"))

        cust = self._q("source_retail",
                       "SELECT customer_id, customer_name, dob, state, mobile, "
                       "email, kyc_status FROM customer_master_raw")
        results.append(self.validate_pattern(
            "MST-V02", DQ, "Customer mobile is a valid 10-digit Indian number",
            cust, "mobile", self.rules["patterns"]["mobile"],
            "customer_master_raw", "Medium", "R-DQ-02"))
        results.append(self._state_normalisation(cust))
        results.append(self._dob_plausibility(cust))

        prod = self._q("source_retail",
                       "SELECT product_code, standard_product_name, standard_product_type, "
                       "product_category, active_flag FROM product_master_raw")
        results.append(self.validate_domain(
            "MST-V07", DQ, "standard_product_type only INSURANCE or MUTUAL_FUND",
            prod, "standard_product_type",
            self.rules["allowed_values"]["standard_product_type"],
            "product_master_raw", "High", "R-DQ-08"))

        rm = self._q("source_retail",
                     "SELECT branch_code, branch_name, city, state, region_name, "
                     "active_flag FROM branch_region_mapping_raw")
        results.append(self.validate_domain(
            "MST-V09", DQ, "region_name within the allowed region domain",
            rm, "region_name", self.rules["allowed_values"]["region_name"],
            "branch_region_mapping_raw", "Medium", "R-DQ-09"))

        results.append(self._orphans("MST-V10", "product_code",
                                     [] if prod is None else prod["product_code"].tolist(),
                                     "product_master_raw", "R-DQ-04", business_date))
        results.append(self._orphans("MST-V11", "customer_id",
                                     [] if cust is None else cust["customer_id"].tolist(),
                                     "customer_master_raw", "R-DQ-05", business_date))
        results.append(self._branch_orphans(
            [] if rm is None else rm["branch_code"].tolist(), business_date))
        results.append(self._conditional_mandatory(business_date))
        return results

    # ==================== helpers ====================
    def _schema_check(self, tc, df, expected, obj):
        res = self._res(tc, LAYER, "Retail source schema matches the expected column list",
                        source_object=obj, expected=len(expected),
                        severity="Medium", risk_ref="R-SS-07")
        if df is None:
            return self._blocked(res, "Source object could not be read")
        actual = list(df.columns)
        missing = [c for c in expected if c not in actual]
        extra = [c for c in actual if c not in expected]
        res.actual = len(actual)
        res.failed_sample = {"missing": missing, "extra": extra}
        res.status = "PASS" if not missing else "FAIL"
        res.message = f"missing={missing or 'none'}, extra={extra or 'none'}"
        res.compute_variance()
        log.info("[%s] %s - %s", tc, res.status, res.message)
        return res

    def _datatypes(self, tc, df, obj):
        res = self._res(tc, LAYER, "Data types and numeric business rules on source",
                        source_object=obj, expected=0, severity="Medium", risk_ref="R-SS-08")
        if df is None:
            return self._blocked(res, "Source object could not be read")
        if df.empty:
            return self._skipped(res, "No rows for the business date")
        required = ["sale_date", "gross_amount", "discount_amount"]
        absent = [c for c in required if c not in df.columns]
        if absent:
            return self._blocked(res, f"Column(s) absent from source: {absent}")
        issues = {}
        dates = pd.to_datetime(df["sale_date"], errors="coerce")
        if dates.isna().any():
            issues["sale_date_invalid"] = int(dates.isna().sum())
        gross = pd.to_numeric(df["gross_amount"], errors="coerce")
        if gross.isna().any():
            issues["gross_not_numeric"] = int(gross.isna().sum())
        if (gross.dropna() <= 0).any():
            issues["gross_not_positive"] = int((gross.dropna() <= 0).sum())
        disc = pd.to_numeric(df["discount_amount"], errors="coerce")
        bad = int(((disc.notna()) & (gross.notna()) & (disc >= gross)).sum())
        if bad:
            issues["discount_ge_gross"] = bad
        res.actual = sum(issues.values())
        res.failed_sample = list(issues.items())
        res.status = "PASS" if not issues else "FAIL"
        res.message = f"data-type or business-rule issues: {issues or 'none'}"
        res.compute_variance()
        log.info("[%s] %s - %s", tc, res.status, res.message)
        return res

    def _batch_tag(self, tc, stg, tbl):
        res = self._res(tc, LAYER, "Every staging row carries load_batch_id and loaded_at",
                        target_object=tbl, expected=0, severity="Low", risk_ref="R-SS-11")
        if stg is None:
            return self._blocked(res, "Staging table could not be read")
        if stg.empty:
            return self._skipped(res, "No staging rows for the business date")
        missing = [c for c in ("load_batch_id", "loaded_at") if c not in stg.columns]
        if missing:
            return self._blocked(res, f"Audit column(s) absent from staging: {missing}")
        n = int(stg["load_batch_id"].isna().sum() + stg["loaded_at"].isna().sum())
        res.actual = n
        res.status = "PASS" if n == 0 else "FAIL"
        res.message = f"{n} row(s) missing load lineage values"
        res.compute_variance()
        log.info("[%s] %s - %s", tc, res.status, res.message)
        return res

    def _null_customer_flag(self, stg, tbl):
        res = self._res("DST-V04", LAYER,
                        "Null customer_id in distributor staging is flagged for DM exclusion",
                        target_object=tbl, severity="High", risk_ref="R-SS-05")
        if stg is None:
            return self._blocked(res, "Staging table could not be read")
        if stg.empty:
            return self._skipped(res, "No staging rows for the business date")
        n = int(stg["customer_id"].isna().sum())
        pct = round(100 * n / len(stg), 2) if len(stg) else 0
        res.expected = "flagged, not propagated as valid"
        res.actual = f"{n} null customer_id ({pct}%)"
        res.status = "PASS"
        res.message = (f"{n} null customer_id row(s) flagged ({pct}%) - "
                       f"must be excluded from the data mart (see DM-V15)")
        res.failed_sample = stg[stg["customer_id"].isna()]["transaction_id"].head(10).tolist()
        res.compute_variance()
        log.info("[DST-V04] %s - %s", res.status, res.message)
        return res

    def _commission_rule(self, stg, tbl):
        res = self._res("DST-V08", LAYER, "commission_amount must not exceed gross_amount",
                        target_object=tbl, expected=0, severity="Medium", risk_ref="R-SS-13")
        if stg is None:
            return self._blocked(res, "Staging table could not be read")
        if stg.empty:
            return self._skipped(res, "No staging rows for the business date")
        if "commission_amount" not in stg.columns:
            return self._blocked(res, "commission_amount column absent from staging")
        c = pd.to_numeric(stg["commission_amount"], errors="coerce")
        g = pd.to_numeric(stg["gross_amount"], errors="coerce")
        bad = stg[(c.notna()) & (g.notna()) & (c > g)]
        res.actual = len(bad)
        res.failed_sample = bad["transaction_id"].head(10).tolist() if not bad.empty else []
        res.status = "PASS" if bad.empty else "FAIL"
        res.message = f"{len(bad)} row(s) with commission greater than gross"
        res.compute_variance()
        log.info("[DST-V08] %s - %s", res.status, res.message)
        return res

    def _api_health(self):
        res = self._res("ONL-V01", LAYER, "Online Sales API health endpoint responds 200",
                        source_object="/api/health", expected=200,
                        severity="High", risk_ref="R-SS-14")
        try:
            r = self.api.health()
            res.actual = r.status_code
            res.status = "PASS" if r.status_code == 200 else "FAIL"
            res.message = f"health endpoint returned HTTP {r.status_code}"
            res.compute_variance()
        except Exception as exc:
            return self._blocked(res, f"API unreachable: {type(exc).__name__}")
        log.info("[ONL-V01] %s - %s", res.status, res.message)
        return res

        def _api_auth(self):
        res = self._res("ONL-V02", LAYER, "API rejects an invalid X-API-Key with HTTP 401",
                        source_object="/api/online-sales", expected=401,
                        severity="Medium", risk_ref="R-SS-15")
        try:
            r = self.api._get(self.api.cfg["endpoint"],
                              params={"from_date": "2026-05-01", "to_date": "2026-05-01"},
                              api_key_override="invalid-key-test")
            res.actual = r.status_code
            if r.status_code == 401:
                res.status = "PASS"
                res.message = "invalid key correctly rejected with HTTP 401"
            elif r.status_code == 200:
                # The endpoint served data despite an invalid key: authentication
                # is not enforced. This is a genuine security finding, not an error.
                res.status = "FAIL"
                res.message = ("API returned HTTP 200 for an invalid X-API-Key - "
                               "authentication is not enforced on this endpoint")
            else:
                res.status = "FAIL"
                res.message = f"invalid key returned HTTP {r.status_code} (expected 401)"
            res.compute_variance()
        except Exception as exc:
            return self._blocked(res, f"API unreachable: {type(exc).__name__}")
        log.info("[ONL-V02] %s - %s", res.status, res.message)
        return res


    def _pagination(self, df, total_reported, api_failed):
        res = self._res("ONL-V03", LAYER,
                        "All API pages retrieved - collected rows equal total_records",
                        source_object="/api/online-sales", expected=total_reported,
                        actual=None if df is None else len(df),
                        severity="Critical", risk_ref="R-SS-06")
        if api_failed:
            return self._blocked(res, "API extraction failed - pagination not verified")
        if total_reported is None:
            return self._blocked(res, "total_records not present in the API envelope")
        res.status = "PASS" if len(df) == total_reported else "FAIL"
        res.message = f"collected={len(df)}, total_records={total_reported}"
        res.compute_variance()
        log.info("[ONL-V03] %s - %s", res.status, res.message)
        return res

    def _json_schema(self, df, api_failed):
        expected = ["transaction_id", "sale_date", "customer_id", "product_code",
                    "product_name_raw", "product_type_raw", "gross_amount",
                    "discount_amount", "transaction_status", "region_code",
                    "payment_mode", "created_at"]
        res = self._res("ONL-V04", LAYER,
                        "API JSON response conforms to the online_sales_raw schema",
                        source_object="/api/online-sales", expected=len(expected),
                        severity="Medium", risk_ref="R-SS-16")
        if api_failed:
            return self._blocked(res, "API extraction failed - schema not verified")
        if df is None or df.empty:
            return self._skipped(res, "No API records returned for the requested range")
        missing = [c for c in expected if c not in df.columns]
        res.actual = len(df.columns)
        res.failed_sample = missing
        res.status = "PASS" if not missing else "FAIL"
        res.message = f"missing fields: {missing or 'none'}"
        res.compute_variance()
        log.info("[ONL-V04] %s - %s", res.status, res.message)
        return res

    def _date_range(self, df, from_date, to_date, api_failed):
        res = self._res("ONL-V09", LAYER,
                        "All API records fall within the requested date range",
                        source_object="/api/online-sales", expected=0,
                        severity="Medium", risk_ref="R-SS-17")
        if api_failed:
            return self._blocked(res, "API extraction failed - date range not verified")
        if df is None or df.empty or "sale_date" not in df.columns:
            return self._skipped(res, "No API records to evaluate")
        d = pd.to_datetime(df["sale_date"], errors="coerce")
        bad = df[(d < pd.to_datetime(from_date)) | (d > pd.to_datetime(to_date))]
        res.actual = len(bad)
        res.status = "PASS" if bad.empty else "FAIL"
        res.message = f"{len(bad)} record(s) outside {from_date}..{to_date}"
        res.compute_variance()
        log.info("[ONL-V09] %s - %s", res.status, res.message)
        return res

    def _state_normalisation(self, cust):
        res = self._res("MST-V03", DQ, "Customer state values normalise to a known state",
                        source_object="customer_master_raw.state",
                        target_object="branch_region_mapping_raw.state",
                        expected=0, severity="High", risk_ref="R-DQ-03")
        rm = self._q("source_retail", "SELECT DISTINCT state FROM branch_region_mapping_raw")
        if cust is None or rm is None:
            return self._blocked(res, "Customer master or region mapping could not be read")
        if cust.empty or rm.empty:
            return self._skipped(res, "No customer or mapping rows to evaluate")
        known = {str(s).strip().upper() for s in rm["state"].dropna()}
        norm = {k.upper(): v.upper() for k, v in self.rules["state_normalisation"].items()}
        unresolved = sorted({str(s).strip() for s in cust["state"].dropna()
                             if str(s).strip().upper() not in known
                             and norm.get(str(s).strip().upper()) not in known})
        res.actual = len(unresolved)
        res.failed_sample = unresolved[:10]
        res.status = "PASS" if not unresolved else "FAIL"
        res.message = f"unresolved state values: {unresolved[:10] or 'none'}"
        res.compute_variance()
        log.info("[MST-V03] %s - %s", res.status, res.message)
        return res

    def _dob_plausibility(self, cust):
        res = self._res("MST-V05", DQ, "Date of birth is present and plausible",
                        source_object="customer_master_raw.dob", expected=0,
                        severity="Low", risk_ref="R-DQ-07")
        if cust is None:
            return self._blocked(res, "Customer master could not be read")
        if cust.empty:
            return self._skipped(res, "No customer rows to evaluate")
        d = pd.to_datetime(cust["dob"], errors="coerce")
        now = pd.Timestamp.now()
        issues = {"null_or_invalid": int(d.isna().sum()),
                  "future_dob": int((d > now).sum()),
                  "age_over_100": int((d < now - pd.Timedelta(days=365.25 * 100)).sum())}
        issues = {k: v for k, v in issues.items() if v}
        res.actual = sum(issues.values())
        res.failed_sample = list(issues.items())
        res.status = "PASS" if not issues else "FAIL"
        res.message = f"date-of-birth issues: {issues or 'none'}"
        res.compute_variance()
        log.info("[MST-V05] %s - %s", res.status, res.message)
        return res

    def _staging_union(self, column, business_date=None):
        """DISTINCT values of `column` across staging, scoped to the date in SQL."""
        frames = []
        for t in ("stg_retail_sales", "stg_distributor_sales", "stg_online_sales"):
            sql = f"SELECT DISTINCT {column} FROM {t} WHERE {column} IS NOT NULL"
            params = None
            if business_date:
                sql += " AND sale_date = :d"
                params = {"d": business_date}
            df = self._q("staging", sql, params)
            if df is not None:
                frames.append(df)
        if not frames:
            return None
        return pd.concat(frames, ignore_index=True).drop_duplicates()

    def _orphans(self, tc, column, parent_values, parent_obj, risk, business_date=None):
        child = self._staging_union(column, business_date)
        if child is None:
            return self._blocked(
                self._res(tc, DQ, f"Every staging {column} exists in {parent_obj}",
                          source_object=f"stg_* {column}", target_object=parent_obj,
                          expected=0, severity="High", risk_ref=risk),
                "Staging tables could not be read")
        return self.validate_reference_integrity(
            tc, DQ, f"Every staging {column} exists in {parent_obj}",
            child[column].dropna().tolist(), parent_values,
            f"stg_* {column}", parent_obj, "High", risk)

    def _branch_orphans(self, parent_values, business_date=None):
        sql = "SELECT DISTINCT branch_code FROM stg_retail_sales WHERE branch_code IS NOT NULL"
        params = None
        if business_date:
            sql += " AND sale_date = :d"
            params = {"d": business_date}
        stg = self._q("staging", sql, params)
        if stg is None:
            return self._blocked(
                self._res("MST-V13", DQ,
                          "Every retail branch_code exists in the branch/region mapping",
                          source_object="stg_retail_sales.branch_code",
                          target_object="branch_region_mapping_raw.branch_code",
                          expected=0, severity="High", risk_ref="R-DQ-04"),
                "Retail staging table could not be read")
        return self.validate_reference_integrity(
            "MST-V13", DQ, "Every retail branch_code exists in the branch/region mapping",
            stg["branch_code"].dropna().tolist(), parent_values,
            "stg_retail_sales.branch_code", "branch_region_mapping_raw.branch_code",
            "High", "R-DQ-04")

    def _conditional_mandatory(self, business_date=None):
        res = self._res("MST-V12", DQ,
                        "policy_number for INSURANCE and folio_number for MUTUAL_FUND populated",
                        source_object="stg_retail_sales / stg_distributor_sales",
                        target_object="product_master_raw", expected=0,
                        severity="Medium", risk_ref="R-DQ-10")
        prod = self._q("source_retail",
                       "SELECT product_code, standard_product_type FROM product_master_raw")
        if prod is None:
            return self._blocked(res, "Product master could not be read")
        breaches = {}
        for tbl in ("stg_retail_sales", "stg_distributor_sales"):
            sql = f"SELECT product_code, policy_number, folio_number FROM {tbl}"
            params = None
            if business_date:
                sql += " WHERE sale_date = :d"
                params = {"d": business_date}
            stg = self._q("staging", sql, params)
            if stg is None:
                return self._blocked(res, f"{tbl} could not be read")
            if stg.empty or prod.empty:
                continue
            m = stg.merge(prod, on="product_code", how="left")
            t = m["standard_product_type"].astype(str).str.upper()
            n_ins = int(m[t == "INSURANCE"]["policy_number"].isna().sum())
            n_mf = int(m[t == "MUTUAL_FUND"]["folio_number"].isna().sum())
            if n_ins:
                breaches[f"{tbl}.policy_number"] = n_ins
            if n_mf:
                breaches[f"{tbl}.folio_number"] = n_mf
        res.actual = sum(breaches.values())
        res.failed_sample = list(breaches.items())
        res.status = "PASS" if not breaches else "FAIL"
        res.message = f"conditional mandatory breaches: {breaches or 'none'}"
        res.compute_variance()
        log.info("[MST-V12] %s - %s", res.status, res.message)
        return res
