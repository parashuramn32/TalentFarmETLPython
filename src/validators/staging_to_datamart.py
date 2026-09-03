"""Layer 2 - Staging to Data Mart (transformation correctness).

dm_sales_transaction (02CreateTables.sql):
  sales_transaction_id PK, source_channel, sale_date, customer_id, customer_name_clean,
  customer_state, region_name, product_code, standard_product_name, standard_product_type,
  product_category, policy_number, folio_number, gross_sales_amount, discount_amount,
  net_sales_amount, commission_amount, transaction_status, load_batch_id, created_at

There is no reversal_amount, zone_name, customer_mobile or product_type_raw column here.
"""
import pandas as pd

from src.validators.base_validator import BaseValidator
from src.utils.config_loader import load_config, load_rules
from src.utils.logger import get_logger

log = get_logger(__name__)
LAYER = "Staging-to-Data Mart"
DQ = "Data Quality"
DM = "dm_sales_transaction"


class StagingToDataMartValidator(BaseValidator):
    def __init__(self, db):
        rules = load_rules()
        super().__init__(tolerance=rules["tolerance"]["amount"])
        self.db, self.rules = db, rules
        self.cfg = load_config("source_config")
        self._salutation_re = (r"^(?:" + "|".join(rules.get("salutations",
                              ["Mr", "Mrs", "Ms", "Dr", "Prof"])) + r")\.?\s+")

    def _q(self, alias, sql, params=None):
        try:
            return self.db.query(alias, sql, params)
        except Exception as exc:
            log.error("Query failed on %s: %s", alias, exc)
            return None

    def _dm(self, business_date, cols="*"):
        return self._q("datamart", f"SELECT {cols} FROM {DM} WHERE sale_date = :d",
                       {"d": business_date})

    def _staging_raw_types(self, business_date):
        """product_code -> product_type_raw, since the DM does not retain the raw label."""
        frames = []
        for t in ("stg_retail_sales", "stg_distributor_sales", "stg_online_sales"):
            df = self._q("staging",
                         f"SELECT DISTINCT product_code, product_type_raw FROM {t} "
                         "WHERE sale_date = :d", {"d": business_date})
            if df is not None:
                frames.append(df)
        if not frames:
            return None
        out = pd.concat(frames, ignore_index=True)
        return out.drop_duplicates(subset=["product_code"]) if not out.empty else out

    def expected_clean_name(self, series):
        """Trim first, then strip a leading salutation case-insensitively.

        Order matters: applying the anchored pattern before trimming means a
        leading space prevents it from matching at all.
        """
        return (series.astype(str)
                .str.strip()
                .str.replace(self._salutation_re, "", regex=True, case=False)
                .str.replace(r"\s+", " ", regex=True)
                .str.strip()
                .str.title())

    # ---------- DM-V01..V04, DM-V13 ----------
    def validate_product_harmonisation(self, business_date):
        results = []
        dm = self._dm(business_date,
                      "sales_transaction_id, product_code, standard_product_name, "
                      "standard_product_type, product_category")
        pm = self._q("source_retail",
                     "SELECT product_code, standard_product_name, standard_product_type, "
                     "product_category, active_flag FROM product_master_raw")

        res = self._res("DM-V01", LAYER,
                        "standard_product_type in the data mart matches the product master",
                        source_object="product_master_raw", target_object=DM,
                        expected=0, severity="Critical", risk_ref="R-DM-01")
        if dm is None or pm is None:
            results.append(self._blocked(res, "Data mart or product master could not be read"))
        elif dm.empty:
            results.append(self._skipped(res, "No data mart rows for the business date"))
        else:
            m = dm.merge(pm, on="product_code", how="left", suffixes=("_dm", "_pm"))
            bad = m[m["standard_product_type_dm"].astype(str).str.upper()
                    != m["standard_product_type_pm"].astype(str).str.upper()]
            res.actual = len(bad)
            res.status = "PASS" if bad.empty else "FAIL"
            res.message = f"{len(bad)} of {len(m)} transaction(s) with a wrong standard_product_type"
            res.failed_sample = bad[["sales_transaction_id", "product_code",
                                     "standard_product_type_dm",
                                     "standard_product_type_pm"]].head(10).to_dict("records")
            res.compute_variance()
            log.info("[DM-V01] %s - %s", res.status, res.message)
            results.append(res)

        raw = self._staging_raw_types(business_date)

        res2 = self._res("DM-V02", LAYER,
                         "ULIP products must map to INSURANCE, not MUTUAL_FUND",
                         source_object="stg_* product_type_raw", target_object=DM,
                         expected=0, severity="Critical", risk_ref="R-DM-02")
        if dm is None or raw is None:
            results.append(self._blocked(res2, "Data mart or staging raw types could not be read"))
        elif dm.empty or raw.empty:
            results.append(self._skipped(res2, "No rows to evaluate"))
        else:
            m = dm.merge(raw, on="product_code", how="inner")
            ulip = m[m["product_type_raw"].astype(str).str.upper() == "ULIP"]
            if ulip.empty:
                results.append(self._skipped(res2, "No ULIP transactions on this business date"))
            else:
                bad = ulip[ulip["standard_product_type"].astype(str).str.upper() != "INSURANCE"]
                res2.actual = len(bad)
                res2.status = "PASS" if bad.empty else "FAIL"
                res2.message = (f"{len(bad)} of {len(ulip)} ULIP transaction(s) "
                                f"not classified as INSURANCE")
                res2.failed_sample = bad["sales_transaction_id"].head(10).tolist()
                res2.compute_variance()
                log.info("[DM-V02] %s - %s", res2.status, res2.message)
                results.append(res2)

        res3 = self._res("DM-V03", LAYER,
                         "Every raw product label maps per the harmonisation reference matrix",
                         source_object="stg_* product_type_raw", target_object=DM,
                         expected=0, severity="Critical", risk_ref="R-DM-01")
        if dm is None or raw is None:
            results.append(self._blocked(res3, "Data mart or staging raw types could not be read"))
        elif dm.empty or raw.empty:
            results.append(self._skipped(res3, "No rows to evaluate"))
        else:
            matrix = {k.upper(): v["type"]
                      for k, v in self.rules["product_harmonisation"].items()}
            m = dm.merge(raw, on="product_code", how="inner")
            breaches = {}
            for label, grp in m.groupby(m["product_type_raw"].astype(str).str.upper()):
                exp = matrix.get(label)
                if exp is None:
                    breaches[label] = "unknown raw label"
                    continue
                bad = grp[grp["standard_product_type"].astype(str).str.upper() != exp]
                if not bad.empty:
                    breaches[label] = f"{len(bad)} not mapped to {exp}"
            res3.actual = len(breaches)
            res3.failed_sample = list(breaches.items())
            res3.status = "PASS" if not breaches else "FAIL"
            res3.message = f"harmonisation breaches: {breaches or 'none'}"
            res3.compute_variance()
            log.info("[DM-V03] %s - %s", res3.status, res3.message)
            results.append(res3)

        res4 = self._res("DM-V04", LAYER,
                         "Data mart uses standard_product_name and product_category from master",
                         source_object="product_master_raw",
                         target_object=f"{DM}.standard_product_name",
                         expected=0, severity="High", risk_ref="R-DM-03")
        if dm is None or pm is None:
            results.append(self._blocked(res4, "Data mart or product master could not be read"))
        elif dm.empty:
            results.append(self._skipped(res4, "No data mart rows for the business date"))
        else:
            m = dm.merge(pm, on="product_code", how="left", suffixes=("_dm", "_pm"))
            bad_name = m[m["standard_product_name_dm"].astype(str).str.strip()
                         != m["standard_product_name_pm"].astype(str).str.strip()]
            bad_cat = m[m["product_category_dm"].astype(str).str.strip()
                        != m["product_category_pm"].astype(str).str.strip()]
            res4.actual = len(bad_name) + len(bad_cat)
            res4.status = "PASS" if bad_name.empty and bad_cat.empty else "FAIL"
            res4.message = (f"{len(bad_name)} standard_product_name and "
                            f"{len(bad_cat)} product_category mismatch(es)")
            res4.failed_sample = bad_name[["sales_transaction_id",
                                           "product_code"]].head(10).to_dict("records")
            res4.compute_variance()
            log.info("[DM-V04] %s - %s", res4.status, res4.message)
            results.append(res4)

        res5 = self._res("DM-V13", LAYER,
                         "No sales against inactive products (active_flag = 0)",
                         source_object="product_master_raw.active_flag", target_object=DM,
                         expected=0, severity="High", risk_ref="R-DM-11")
        if dm is None or pm is None:
            results.append(self._blocked(res5, "Data mart or product master could not be read"))
        elif dm.empty:
            results.append(self._skipped(res5, "No data mart rows for the business date"))
        else:
            m = dm.merge(pm[["product_code", "active_flag"]], on="product_code", how="left")
            bad = m[pd.to_numeric(m["active_flag"], errors="coerce") == 0]
            res5.actual = len(bad)
            res5.status = "PASS" if bad.empty else "FAIL"
            res5.message = f"{len(bad)} transaction(s) reference discontinued products"
            res5.failed_sample = bad[["sales_transaction_id",
                                      "product_code"]].head(10).to_dict("records")
            res5.compute_variance()
            log.info("[DM-V13] %s - %s", res5.status, res5.message)
            results.append(res5)
        return results

    # ---------- DM-V05 ----------
    def validate_channel_mapping(self, business_date):
        res = self._res("DM-V05", LAYER,
                        "source_channel holds standardised Retail / Distributor / Online values",
                        source_object="stg_* (channel of origin)",
                        target_object=f"{DM}.source_channel",
                        expected=0, severity="High", risk_ref="R-DM-04")
        dm = self._dm(business_date, "sales_transaction_id, source_channel")
        if dm is None:
            return self._blocked(res, "Data mart could not be read")
        if dm.empty:
            return self._skipped(res, "No data mart rows for the business date")
        allowed = {v.upper() for v in self.rules["allowed_values"]["source_channel"]}
        bad = dm[~dm["source_channel"].astype(str).str.strip().str.upper().isin(allowed)]
        res.actual = len(bad)
        res.status = "PASS" if bad.empty else "FAIL"
        res.message = f"{len(bad)} row(s) with a non-standard source_channel"
        res.failed_sample = bad[["sales_transaction_id",
                                 "source_channel"]].head(10).to_dict("records")
        res.compute_variance()
        log.info("[DM-V05] %s - %s", res.status, res.message)
        return res

    # ---------- DM-V06..V08, DM-V16 ----------
    def validate_region_mapping(self, business_date):
        results = []
        rm = self._q("source_retail",
                     "SELECT branch_code, state, region_name FROM branch_region_mapping_raw")

        res = self._res("DM-V06", LAYER,
                        "Retail region derived from branch_code via the branch/region mapping",
                        source_object="stg_retail_sales.branch_code + branch_region_mapping_raw",
                        target_object=f"{DM}.region_name",
                        expected=0, severity="Critical", risk_ref="R-DM-05")
        stg = self._q("staging",
                      "SELECT transaction_id, branch_code FROM stg_retail_sales "
                      "WHERE sale_date = :d", {"d": business_date})
        dm = self._q("datamart",
                     "SELECT sales_transaction_id, region_name FROM dm_sales_transaction "
                     "WHERE sale_date = :d AND source_channel = 'Retail'", {"d": business_date})
        if stg is None or dm is None or rm is None:
            results.append(self._blocked(res, "Staging, data mart or mapping could not be read"))
        elif stg.empty or dm.empty:
            results.append(self._skipped(res, "No retail rows for the business date"))
        else:
            dm = dm.copy()
            dm["_txn"] = dm["sales_transaction_id"].astype(str).str.split("_").str[-1]
            m = dm.merge(stg, left_on="_txn", right_on="transaction_id", how="inner") \
                  .merge(rm[["branch_code", "region_name"]], on="branch_code",
                         how="left", suffixes=("_dm", "_map"))
            if m.empty:
                results.append(self._blocked(
                    res, "Could not join the data mart to staging on the surrogate key pattern "
                         "(expected sales_transaction_id to end with the source transaction_id)"))
            else:
                bad = m[(m["region_name_map"].notna()) &
                        (m["region_name_dm"].astype(str).str.upper()
                         != m["region_name_map"].astype(str).str.upper())]
                unmapped = m[m["region_name_map"].isna()]
                res.actual = len(bad)
                res.status = "PASS" if bad.empty else "FAIL"
                res.message = (f"{len(bad)} incorrect region(s) of {len(m)} joined; "
                               f"{len(unmapped)} branch_code not present in the mapping")
                res.failed_sample = bad[["sales_transaction_id", "branch_code",
                                         "region_name_dm",
                                         "region_name_map"]].head(10).to_dict("records")
                res.compute_variance()
                log.info("[DM-V06] %s - %s", res.status, res.message)
                results.append(res)

        res2 = self._res("DM-V07", LAYER, "Distributor region derived from region_code",
                         source_object="stg_distributor_sales.region_code",
                         target_object=f"{DM}.region_name",
                         expected=0, severity="High", risk_ref="R-DM-05")
        dm_d = self._q("datamart",
                       "SELECT sales_transaction_id, region_name FROM dm_sales_transaction "
                       "WHERE sale_date = :d AND source_channel = 'Distributor'",
                       {"d": business_date})
        if dm_d is None:
            results.append(self._blocked(res2, "Data mart could not be read"))
        elif dm_d.empty:
            results.append(self._skipped(res2, "No distributor rows for the business date"))
        else:
            allowed = {a.upper() for a in self.rules["allowed_values"]["region_name"]}
            bad = dm_d[~dm_d["region_name"].astype(str).str.upper().isin(allowed)]
            unknown = dm_d[dm_d["region_name"].astype(str).str.upper() == "UNKNOWN_REGION"]
            res2.actual = len(bad)
            res2.status = "PASS" if bad.empty else "FAIL"
            res2.message = (f"{len(bad)} invalid region value(s); "
                            f"{len(unknown)} fell back to UNKNOWN_REGION")
            res2.failed_sample = bad.head(10).to_dict("records")
            res2.compute_variance()
            log.info("[DM-V07] %s - %s", res2.status, res2.message)
            results.append(res2)

        res3 = self._res("DM-V08", LAYER,
                         "Online region derived from the normalised customer_state",
                         source_object="customer_master_raw.state + branch_region_mapping_raw",
                         target_object=f"{DM}.region_name",
                         expected=0, severity="High", risk_ref="R-DM-06")
        dm_o = self._q("datamart",
                       "SELECT sales_transaction_id, customer_state, region_name "
                       "FROM dm_sales_transaction WHERE sale_date = :d "
                       "AND source_channel = 'Online'", {"d": business_date})
        if dm_o is None or rm is None:
            results.append(self._blocked(res3, "Data mart or mapping could not be read"))
        elif dm_o.empty:
            results.append(self._skipped(res3, "No online rows for the business date"))
        else:
            smap = (rm.dropna(subset=["state"])
                      .assign(_s=lambda x: x["state"].astype(str).str.strip().str.upper())
                      .drop_duplicates("_s").set_index("_s")["region_name"].str.upper().to_dict())
            norm = {k.upper(): v.upper() for k, v in self.rules["state_normalisation"].items()}

            def expected_region(state):
                s = str(state).strip().upper()
                return smap.get(norm.get(s, s), "UNKNOWN_REGION")

            dm_o = dm_o.copy()
            dm_o["_exp"] = dm_o["customer_state"].apply(expected_region)
            bad = dm_o[dm_o["region_name"].astype(str).str.upper() != dm_o["_exp"]]
            res3.actual = len(bad)
            res3.status = "PASS" if bad.empty else "FAIL"
            res3.message = (f"{len(bad)} of {len(dm_o)} online transaction(s) "
                            f"with an incorrect region")
            res3.failed_sample = bad[["sales_transaction_id", "customer_state",
                                      "region_name", "_exp"]].head(10).to_dict("records")
            res3.compute_variance()
            log.info("[DM-V08] %s - %s", res3.status, res3.message)
            results.append(res3)

        threshold = self.rules["thresholds"]["unknown_region_pct"]
        res4 = self._res("DM-V16", LAYER,
                         "UNKNOWN_REGION volume within the agreed threshold",
                         target_object=f"{DM}.region_name", expected=threshold,
                         severity="Medium", risk_ref="R-DM-14")
        unk = self._q("datamart",
                      "SELECT COUNT(*) AS cnt, COALESCE(SUM(net_sales_amount),0) AS val "
                      "FROM dm_sales_transaction WHERE sale_date = :d "
                      "AND region_name = 'UNKNOWN_REGION'", {"d": business_date})
        try:
            tot = self.db.count("datamart", DM, "sale_date = :d", {"d": business_date})
        except Exception:
            tot = None
        if unk is None or tot is None:
            results.append(self._blocked(res4, "Data mart could not be read"))
        elif tot == 0:
            results.append(self._skipped(res4, "No data mart rows for the business date"))
        else:
            cnt, val = int(unk.iloc[0]["cnt"]), float(unk.iloc[0]["val"])
            pct = round(100 * cnt / tot, 2)
            res4.actual = pct
            res4.status = "PASS" if pct < threshold else "FAIL"
            res4.message = (f"{cnt} of {tot} transaction(s) UNKNOWN_REGION = {pct}% "
                            f"(threshold {threshold}%), value {val}")
            res4.compute_variance()
            log.info("[DM-V16] %s - %s", res4.status, res4.message)
            results.append(res4)
        return results

    # ---------- DM-V10 ----------
    def validate_customer_cleansing(self, business_date):
        res = self._res("DM-V10", LAYER,
                        "customer_name_clean is trimmed, salutation-stripped and title-cased",
                        source_object="customer_master_raw.customer_name",
                        target_object=f"{DM}.customer_name_clean",
                        expected=0, severity="Medium", risk_ref="R-DM-08")
        dm = self._q("datamart",
                     "SELECT sales_transaction_id, customer_id, customer_name_clean "
                     "FROM dm_sales_transaction WHERE sale_date = :d "
                     "AND customer_id IS NOT NULL", {"d": business_date})
        cm = self._q("source_retail",
                     "SELECT customer_id, customer_name FROM customer_master_raw")
        if dm is None or cm is None:
            return self._blocked(res, "Data mart or customer master could not be read")
        if dm.empty or cm.empty:
            return self._skipped(res, "No rows to evaluate")
        m = dm.merge(cm, on="customer_id", how="inner")
        if m.empty:
            return self._skipped(res, "No customers joined to the master")
        expected = self.expected_clean_name(m["customer_name"])
        bad = m[m["customer_name_clean"].astype(str).str.strip() != expected]
        res.actual = len(bad)
        res.status = "PASS" if bad.empty else "FAIL"
        res.message = f"{len(bad)} of {len(m)} customer name(s) not correctly cleansed"
        res.failed_sample = bad[["customer_id", "customer_name_clean",
                                 "customer_name"]].head(10).to_dict("records")
        res.compute_variance()
        log.info("[DM-V10] %s - %s", res.status, res.message)
        return res

    # ---------- DM-V11 ----------
    def validate_customer_state(self, business_date):
        res = self._res("DM-V11", LAYER, "customer_state enriched from the customer master",
                        source_object="customer_master_raw.state",
                        target_object=f"{DM}.customer_state",
                        expected=0, severity="Medium", risk_ref="R-DM-09")
        dm = self._q("datamart",
                     "SELECT sales_transaction_id, customer_id, customer_state "
                     "FROM dm_sales_transaction WHERE sale_date = :d "
                     "AND customer_id IS NOT NULL", {"d": business_date})
        cm = self._q("source_retail", "SELECT customer_id, state FROM customer_master_raw")
        if dm is None or cm is None:
            return self._blocked(res, "Data mart or customer master could not be read")
        if dm.empty or cm.empty:
            return self._skipped(res, "No rows to evaluate")
        m = dm.merge(cm, on="customer_id", how="inner")
        bad = m[m["customer_state"].isna() & m["state"].notna()]
        res.actual = len(bad)
        res.status = "PASS" if bad.empty else "FAIL"
        res.message = f"{len(bad)} of {len(m)} transaction(s) missing customer_state"
        res.compute_variance()
        log.info("[DM-V11] %s - %s", res.status, res.message)
        return res

    # ---------- DM-V12 ----------
    def validate_net_amount(self, business_date):
        res = self._res("DM-V12", LAYER,
                        "net_sales_amount equals gross_sales_amount minus discount_amount",
                        source_object="stg_* gross/discount",
                        target_object=f"{DM}.net_sales_amount",
                        expected=0, severity="Critical", risk_ref="R-DM-10")
        dm = self._q("datamart",
                     "SELECT sales_transaction_id, gross_sales_amount, "
                     "COALESCE(discount_amount,0) AS discount_amount, net_sales_amount "
                     "FROM dm_sales_transaction WHERE sale_date = :d", {"d": business_date})
        if dm is None:
            return self._blocked(res, "Data mart could not be read")
        if dm.empty:
            return self._skipped(res, "No data mart rows for the business date")
        dm = dm.copy()
        for c in ("gross_sales_amount", "discount_amount", "net_sales_amount"):
            dm[c] = pd.to_numeric(dm[c], errors="coerce").fillna(0)
        dm["_expected"] = dm["gross_sales_amount"] - dm["discount_amount"]
        bad = dm[(dm["_expected"] - dm["net_sales_amount"]).abs() > self.tolerance]
        res.actual = len(bad)
        res.status = "PASS" if bad.empty else "FAIL"
        res.message = f"{len(bad)} of {len(dm)} transaction(s) with an incorrect net_sales_amount"
        res.failed_sample = bad[["sales_transaction_id", "gross_sales_amount",
                                 "discount_amount", "net_sales_amount",
                                 "_expected"]].head(10).to_dict("records")
        res.compute_variance()
        log.info("[DM-V12] %s - %s", res.status, res.message)
        return res

    # ---------- DM-V14 / V15 / V17 ----------
    def validate_completeness(self, business_date):
        results = []
        r14 = self._res("DM-V14", LAYER,
                        "Valid staging total vs data mart transaction count",
                        source_object="stg_* (all channels)", target_object=DM,
                        severity="Critical", risk_ref="R-DM-12")
        try:
            stg_total = 0
            for t, s in (("stg_retail_sales", "COMPLETED"),
                         ("stg_distributor_sales", "APPROVED"),
                         ("stg_online_sales", "COMPLETED")):
                stg_total += self.db.count(
                    "staging", t, "sale_date = :d AND UPPER(transaction_status) = :s",
                    {"d": business_date, "s": s})
            dm_total = self.db.count("datamart", DM, "sale_date = :d", {"d": business_date})
            results.append(self.validate_count(
                "DM-V14", LAYER, "Valid staging total vs data mart transaction count",
                stg_total, dm_total, "stg_* (all channels)", DM, "Critical", "R-DM-12"))
        except Exception as exc:
            results.append(self._blocked(r14, f"Count failed: {type(exc).__name__}"))

        res = self._res("DM-V15", LAYER,
                        "Null-customer transactions must not count as valid net sales",
                        source_object="stg_distributor_sales", target_object=DM,
                        expected=0, severity="High", risk_ref="R-DM-13")
        bad = self._q("datamart",
                      "SELECT sales_transaction_id FROM dm_sales_transaction "
                      "WHERE sale_date = :d AND (customer_id IS NULL OR customer_id = '') "
                      "AND net_sales_amount > 0", {"d": business_date})
        if bad is None:
            results.append(self._blocked(res, "Data mart could not be read"))
        else:
            res.actual = len(bad)
            res.status = "PASS" if bad.empty else "FAIL"
            res.message = f"{len(bad)} null-customer transaction(s) contributing to net sales"
            res.failed_sample = (bad["sales_transaction_id"].head(10).tolist()
                                 if not bad.empty else [])
            res.compute_variance()
            log.info("[DM-V15] %s - %s", res.status, res.message)
            results.append(res)

        results.append(self.validate_duplicates(
            "DM-V17", LAYER, "sales_transaction_id is unique in the data mart",
            self._dm(business_date, "sales_transaction_id"), "sales_transaction_id",
            DM, "Critical", "R-DM-12"))
        return results

    # ---------- MST-V04 ----------
    def validate_kyc_compliance(self, business_date):
        res = self._res("MST-V04", DQ,
                        "REJECTED-KYC customers must not contribute to net sales",
                        source_object="customer_master_raw.kyc_status", target_object=DM,
                        expected=0, severity="Critical", risk_ref="R-DQ-06")
        dm = self._q("datamart",
                     "SELECT sales_transaction_id, customer_id, net_sales_amount "
                     "FROM dm_sales_transaction WHERE sale_date = :d "
                     "AND customer_id IS NOT NULL", {"d": business_date})
        cm = self._q("source_retail",
                     "SELECT customer_id FROM customer_master_raw "
                     "WHERE UPPER(kyc_status) = 'REJECTED'")
        if dm is None or cm is None:
            return self._blocked(res, "Data mart or customer master could not be read")
        if dm.empty:
            return self._skipped(res, "No data mart rows for the business date")
        bad = dm[dm["customer_id"].isin(cm["customer_id"])] if not cm.empty else pd.DataFrame()
        value = (float(pd.to_numeric(bad["net_sales_amount"], errors="coerce").sum())
                 if not bad.empty else 0.0)
        res.actual = len(bad)
        res.status = "PASS" if bad.empty else "FAIL"
        res.message = (f"{len(bad)} REJECTED-KYC transaction(s) contributing "
                       f"{value} to net sales")
        res.failed_sample = (bad[["sales_transaction_id",
                                  "customer_id"]].head(10).to_dict("records")
                             if not bad.empty else [])
        res.compute_variance()
        log.info("[MST-V04] %s - %s", res.status, res.message)
        return res
