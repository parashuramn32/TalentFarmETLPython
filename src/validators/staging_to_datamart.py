"""Layer 2 validators - Staging to Data Mart (transformation correctness).

dm_sales_transaction columns (02CreateTables.sql):
  sales_transaction_id PK, source_channel, sale_date, customer_id, customer_name_clean,
  customer_state, region_name, product_code, standard_product_name, standard_product_type,
  product_category, policy_number, folio_number, gross_sales_amount, discount_amount,
  net_sales_amount, commission_amount, transaction_status, load_batch_id, created_at

No reversal_amount, zone_name, customer_mobile or product_type_raw column exists here.
"""
import pandas as pd

from src.validators.base_validator import BaseValidator
from src.utils.config_loader import load_config, load_rules
from src.utils.result import ValidationResult
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

    def _dm(self, business_date, cols="*"):
        return self.db.query("datamart", f"SELECT {cols} FROM {DM} WHERE sale_date = :d",
                             {"d": business_date})

    def _staging_raw_types(self, business_date):
        """Distinct product_code -> product_type_raw across all staging channels."""
        frames = []
        for t in ("stg_retail_sales", "stg_distributor_sales", "stg_online_sales"):
            frames.append(self.db.query(
                "staging",
                f"SELECT DISTINCT product_code, product_type_raw FROM {t} WHERE sale_date = :d",
                {"d": business_date}))
        out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        return out.drop_duplicates(subset=["product_code"]) if not out.empty else out

    # ---------- DM-V01..V04, DM-V13 ----------
    def validate_product_harmonisation(self, business_date):
        results = []
        dm = self._dm(business_date,
                      "sales_transaction_id, product_code, standard_product_name, "
                      "standard_product_type, product_category")
        pm = self.db.query("source_retail",
                           "SELECT product_code, standard_product_name, standard_product_type, "
                           "product_category, active_flag FROM product_master_raw")

        res = ValidationResult(
            test_case_id="DM-V01", layer=LAYER,
            description="standard_product_type in data mart matches product master",
            source_object="product_master_raw", target_object=DM,
            expected="100% match with master", severity="Critical", risk_ref="R-DM-01")
        if dm.empty or pm.empty:
            res.status, res.message = "SKIPPED", "No data mart or product master rows"
        else:
            m = dm.merge(pm, on="product_code", how="left", suffixes=("_dm", "_pm"))
            bad = m[m["standard_product_type_dm"].astype(str).str.upper()
                    != m["standard_product_type_pm"].astype(str).str.upper()]
            res.actual = f"{len(bad)} mismatch(es)"
            res.status = "PASS" if bad.empty else "FAIL"
            res.message = f"{len(bad)} transaction(s) with wrong standard_product_type"
            res.failed_sample = bad[["sales_transaction_id", "product_code",
                                     "standard_product_type_dm",
                                     "standard_product_type_pm"]].head(10).to_dict("records")
        log.info("[DM-V01] %s - %s", res.status, res.message)
        results.append(res)

        res2 = ValidationResult(
            test_case_id="DM-V02", layer=LAYER,
            description="ULIP products must map to INSURANCE (not MUTUAL_FUND)",
            source_object="stg_* product_type_raw", target_object=DM,
            expected="INSURANCE", severity="Critical", risk_ref="R-DM-02")
        try:
            raw = self._staging_raw_types(business_date)
            if dm.empty or raw.empty:
                res2.status, res2.message = "SKIPPED", "No staging raw type data"
            else:
                m = dm.merge(raw, on="product_code", how="inner")
                ulip = m[m["product_type_raw"].astype(str).str.upper() == "ULIP"]
                bad = ulip[ulip["standard_product_type"].astype(str).str.upper() != "INSURANCE"]
                res2.actual = f"{len(bad)} mis-mapped of {len(ulip)} ULIP row(s)"
                res2.status = "PASS" if bad.empty else "FAIL"
                res2.message = f"{len(bad)} ULIP transaction(s) not classified as INSURANCE"
                res2.failed_sample = bad["sales_transaction_id"].head(10).tolist()
        except Exception as exc:
            res2.status, res2.message = "ERROR", str(exc)[:200]
        log.info("[DM-V02] %s - %s", res2.status, res2.message)
        results.append(res2)

        res3 = ValidationResult(
            test_case_id="DM-V03", layer=LAYER,
            description="Every raw product label maps per harmonisation reference matrix",
            source_object="stg_* product_type_raw", target_object=DM,
            expected="all raw labels map correctly", severity="Critical", risk_ref="R-DM-01")
        try:
            raw = self._staging_raw_types(business_date)
            if dm.empty or raw.empty:
                res3.status, res3.message = "SKIPPED", "No staging raw type data"
            else:
                matrix = {k.upper(): v["type"]
                          for k, v in self.rules["product_harmonisation"].items()}
                m = dm.merge(raw, on="product_code", how="inner")
                breaches = {}
                for label, grp in m.groupby(m["product_type_raw"].astype(str).str.upper()):
                    exp = matrix.get(label)
                    if exp is None:
                        breaches[label] = "UNKNOWN RAW LABEL"
                        continue
                    bad = grp[grp["standard_product_type"].astype(str).str.upper() != exp]
                    if not bad.empty:
                        breaches[label] = f"{len(bad)} not mapped to {exp}"
                res3.actual = breaches or "all labels correct"
                res3.status = "PASS" if not breaches else "FAIL"
                res3.message = f"harmonisation breaches: {breaches or 'none'}"
        except Exception as exc:
            res3.status, res3.message = "ERROR", str(exc)[:200]
        log.info("[DM-V03] %s - %s", res3.status, res3.message)
        results.append(res3)

        res4 = ValidationResult(
            test_case_id="DM-V04", layer=LAYER,
            description="Data mart uses standard_product_name and product_category from master",
            source_object="product_master_raw", target_object=f"{DM}.standard_product_name",
            expected="values equal master", severity="High", risk_ref="R-DM-03")
        if dm.empty or pm.empty:
            res4.status, res4.message = "SKIPPED", "No data"
        else:
            m = dm.merge(pm, on="product_code", how="left", suffixes=("_dm", "_pm"))
            bad_name = m[m["standard_product_name_dm"].astype(str).str.strip()
                         != m["standard_product_name_pm"].astype(str).str.strip()]
            bad_cat = m[m["product_category_dm"].astype(str).str.strip()
                        != m["product_category_pm"].astype(str).str.strip()]
            res4.actual = f"{len(bad_name)} name, {len(bad_cat)} category mismatch(es)"
            res4.status = "PASS" if bad_name.empty and bad_cat.empty else "FAIL"
            res4.message = (f"{len(bad_name)} standard_product_name and "
                            f"{len(bad_cat)} product_category mismatch(es)")
            res4.failed_sample = bad_name[["sales_transaction_id",
                                           "product_code"]].head(10).to_dict("records")
        log.info("[DM-V04] %s - %s", res4.status, res4.message)
        results.append(res4)

        res5 = ValidationResult(
            test_case_id="DM-V13", layer=LAYER,
            description="No sales against inactive products (active_flag = 0)",
            source_object="product_master_raw.active_flag", target_object=DM,
            expected="0 inactive-product sales", severity="High", risk_ref="R-DM-11")
        if dm.empty or pm.empty:
            res5.status, res5.message = "SKIPPED", "No data"
        else:
            m = dm.merge(pm[["product_code", "active_flag"]], on="product_code", how="left")
            bad = m[pd.to_numeric(m["active_flag"], errors="coerce") == 0]
            res5.actual = f"{len(bad)} inactive-product sale(s)"
            res5.status = "PASS" if bad.empty else "FAIL"
            res5.message = f"{len(bad)} transaction(s) reference discontinued products"
            res5.failed_sample = bad[["sales_transaction_id",
                                      "product_code"]].head(10).to_dict("records")
        log.info("[DM-V13] %s - %s", res5.status, res5.message)
        results.append(res5)
        return results

    # ---------- DM-V05 ----------
    def validate_channel_mapping(self, business_date):
        res = ValidationResult(
            test_case_id="DM-V05", layer=LAYER,
            description="source_channel holds standardised Retail / Distributor / Online values",
            source_object="stg_* (channel of origin)", target_object=f"{DM}.source_channel",
            expected=self.rules["allowed_values"]["source_channel"],
            severity="High", risk_ref="R-DM-04")
        dm = self._dm(business_date, "sales_transaction_id, source_channel")
        if dm.empty:
            res.status, res.message = "SKIPPED", "No data mart rows"
            return res
        allowed = {v.upper() for v in self.rules["allowed_values"]["source_channel"]}
        bad = dm[~dm["source_channel"].astype(str).str.strip().str.upper().isin(allowed)]
        res.actual = f"{len(bad)} invalid channel value(s)"
        res.status = "PASS" if bad.empty else "FAIL"
        res.message = f"{len(bad)} row(s) with a non-standard source_channel"
        res.failed_sample = bad[["sales_transaction_id",
                                 "source_channel"]].head(10).to_dict("records")
        log.info("[DM-V05] %s - %s", res.status, res.message)
        return res

    # ---------- DM-V06..V08, DM-V16 ----------
    def validate_region_mapping(self, business_date):
        results = []
        rm = self.db.query("source_retail",
                           "SELECT branch_code, state, region_name FROM branch_region_mapping_raw")

        res = ValidationResult(
            test_case_id="DM-V06", layer=LAYER,
            description="Retail region derived from branch_code via branch/region mapping",
            source_object="stg_retail_sales.branch_code + branch_region_mapping_raw",
            target_object=f"{DM}.region_name",
            expected="region_name from mapping", severity="Critical", risk_ref="R-DM-05")
        try:
            stg = self.db.query(
                "staging",
                "SELECT transaction_id, branch_code FROM stg_retail_sales WHERE sale_date = :d",
                {"d": business_date})
            dm = self.db.query(
                "datamart",
                "SELECT sales_transaction_id, region_name FROM dm_sales_transaction "
                "WHERE sale_date = :d AND source_channel = 'Retail'", {"d": business_date})
            if stg.empty or dm.empty or rm.empty:
                res.status, res.message = "SKIPPED", "No retail staging / data mart / mapping rows"
            else:
                # sales_transaction_id is a prefixed surrogate key; match the trailing txn id
                dm["_txn"] = dm["sales_transaction_id"].astype(str).str.split("_").str[-1]
                m = dm.merge(stg, left_on="_txn", right_on="transaction_id", how="inner") \
                      .merge(rm[["branch_code", "region_name"]], on="branch_code",
                             how="left", suffixes=("_dm", "_map"))
                bad = m[(m["region_name_map"].notna()) &
                        (m["region_name_dm"].astype(str).str.upper()
                         != m["region_name_map"].astype(str).str.upper())]
                unmapped = m[m["region_name_map"].isna()]
                res.actual = f"{len(bad)} wrong, {len(unmapped)} unmapped branch(es)"
                res.status = "PASS" if bad.empty else "FAIL"
                res.message = (f"{len(bad)} incorrect region(s); "
                               f"{len(unmapped)} branch_code not present in mapping")
                res.failed_sample = bad[["sales_transaction_id", "branch_code",
                                         "region_name_dm",
                                         "region_name_map"]].head(10).to_dict("records")
        except Exception as exc:
            res.status, res.message = "ERROR", str(exc)[:200]
        log.info("[DM-V06] %s - %s", res.status, res.message)
        results.append(res)

        res2 = ValidationResult(
            test_case_id="DM-V07", layer=LAYER,
            description="Distributor region derived from region_code",
            source_object="stg_distributor_sales.region_code", target_object=f"{DM}.region_name",
            expected="valid region or UNKNOWN_REGION", severity="High", risk_ref="R-DM-05")
        dm_d = self.db.query(
            "datamart",
            "SELECT sales_transaction_id, region_name FROM dm_sales_transaction "
            "WHERE sale_date = :d AND source_channel = 'Distributor'", {"d": business_date})
        if dm_d.empty:
            res2.status, res2.message = "SKIPPED", "No distributor data mart rows"
        else:
            allowed = {a.upper() for a in self.rules["allowed_values"]["region_name"]}
            bad = dm_d[~dm_d["region_name"].astype(str).str.upper().isin(allowed)]
            unknown = dm_d[dm_d["region_name"].astype(str).str.upper() == "UNKNOWN_REGION"]
            res2.actual = f"{len(bad)} invalid, {len(unknown)} UNKNOWN_REGION"
            res2.status = "PASS" if bad.empty else "FAIL"
            res2.message = f"{len(bad)} invalid region value(s); {len(unknown)} UNKNOWN_REGION"
        log.info("[DM-V07] %s - %s", res2.status, res2.message)
        results.append(res2)

        res3 = ValidationResult(
            test_case_id="DM-V08", layer=LAYER,
            description="Online region derived from normalised customer_state",
            source_object="customer_master_raw.state + branch_region_mapping_raw",
            target_object=f"{DM}.region_name",
            expected="region from normalised state", severity="High", risk_ref="R-DM-06")
        dm_o = self.db.query(
            "datamart",
            "SELECT sales_transaction_id, customer_state, region_name FROM dm_sales_transaction "
            "WHERE sale_date = :d AND source_channel = 'Online'", {"d": business_date})
        if dm_o.empty or rm.empty:
            res3.status, res3.message = "SKIPPED", "No online data mart rows"
        else:
            smap = (rm.dropna(subset=["state"])
                      .assign(_s=lambda x: x["state"].astype(str).str.strip().str.upper())
                      .drop_duplicates("_s").set_index("_s")["region_name"].str.upper().to_dict())
            norm = {k.upper(): v.upper() for k, v in self.rules["state_normalisation"].items()}

            def expected_region(state):
                s = str(state).strip().upper()
                return smap.get(norm.get(s, s), "UNKNOWN_REGION")

            dm_o["_exp"] = dm_o["customer_state"].apply(expected_region)
            bad = dm_o[dm_o["region_name"].astype(str).str.upper() != dm_o["_exp"]]
            res3.actual = f"{len(bad)} mismatch(es) of {len(dm_o)}"
            res3.status = "PASS" if bad.empty else "FAIL"
            res3.message = f"{len(bad)} online transaction(s) with incorrect region derivation"
            res3.failed_sample = bad[["sales_transaction_id", "customer_state",
                                      "region_name", "_exp"]].head(10).to_dict("records")
        log.info("[DM-V08] %s - %s", res3.status, res3.message)
        results.append(res3)

        res4 = ValidationResult(
            test_case_id="DM-V16", layer=LAYER,
            description="UNKNOWN_REGION volume and value within agreed threshold",
            target_object=f"{DM}.region_name",
            expected=f"< {self.rules['thresholds']['unknown_region_pct']}% of volume",
            severity="Medium", risk_ref="R-DM-14")
        unk = self.db.query(
            "datamart",
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(net_sales_amount),0) AS val "
            "FROM dm_sales_transaction WHERE sale_date = :d AND region_name = 'UNKNOWN_REGION'",
            {"d": business_date})
        tot = self.db.count("datamart", DM, "sale_date = :d", {"d": business_date})
        if unk.empty or tot == 0:
            res4.status, res4.message = "SKIPPED", "No data mart rows"
        else:
            cnt, val = int(unk.iloc[0]["cnt"]), float(unk.iloc[0]["val"])
            pct = round(100 * cnt / tot, 2)
            res4.actual = f"{cnt} txn ({pct}%), value {val}"
            res4.status = "PASS" if pct < self.rules["thresholds"]["unknown_region_pct"] else "FAIL"
            res4.message = f"{cnt} UNKNOWN_REGION transaction(s) = {pct}% of volume, value {val}"
        log.info("[DM-V16] %s - %s", res4.status, res4.message)
        results.append(res4)
        return results

    # ---------- DM-V10 ----------
    def validate_customer_cleansing(self, business_date):
        res = ValidationResult(
            test_case_id="DM-V10", layer=LAYER,
            description="customer_name_clean is trimmed and title-cased from master",
            source_object="customer_master_raw.customer_name",
            target_object=f"{DM}.customer_name_clean",
            expected="TRIM + TITLE CASE", severity="Medium", risk_ref="R-DM-08")
        dm = self.db.query(
            "datamart",
            "SELECT sales_transaction_id, customer_id, customer_name_clean "
            "FROM dm_sales_transaction WHERE sale_date = :d AND customer_id IS NOT NULL",
            {"d": business_date})
        cm = self.db.query("source_retail",
                           "SELECT customer_id, customer_name FROM customer_master_raw")
        if dm.empty or cm.empty:
            res.status, res.message = "SKIPPED", "No data mart or customer master rows"
            return res
        m = dm.merge(cm, on="customer_id", how="inner")
        expected = (m["customer_name"].astype(str)
                    .str.replace(r"^(Mr\.?|Mrs\.?|Ms\.?|Dr\.?)\s*", "", regex=True)
                    .str.strip().str.replace(r"\s+", " ", regex=True).str.title())
        bad = m[m["customer_name_clean"].astype(str).str.strip() != expected]
        res.actual = f"{len(bad)} mismatch(es) of {len(m)}"
        res.status = "PASS" if bad.empty else "FAIL"
        res.message = f"{len(bad)} customer name(s) not correctly cleansed"
        res.failed_sample = bad[["customer_id", "customer_name_clean",
                                 "customer_name"]].head(10).to_dict("records")
        log.info("[DM-V10] %s - %s", res.status, res.message)
        return res

    # ---------- DM-V11 ----------
    def validate_customer_state(self, business_date):
        res = ValidationResult(
            test_case_id="DM-V11", layer=LAYER,
            description="customer_state enriched from customer master",
            source_object="customer_master_raw.state", target_object=f"{DM}.customer_state",
            expected="state equals master value", severity="Medium", risk_ref="R-DM-09")
        dm = self.db.query(
            "datamart",
            "SELECT sales_transaction_id, customer_id, customer_state FROM dm_sales_transaction "
            "WHERE sale_date = :d AND customer_id IS NOT NULL", {"d": business_date})
        cm = self.db.query("source_retail", "SELECT customer_id, state FROM customer_master_raw")
        if dm.empty or cm.empty:
            res.status, res.message = "SKIPPED", "No data mart or customer master rows"
            return res
        m = dm.merge(cm, on="customer_id", how="inner")
        bad = m[m["customer_state"].isna() & m["state"].notna()]
        res.actual = f"{len(bad)} unenriched row(s) of {len(m)}"
        res.status = "PASS" if bad.empty else "FAIL"
        res.message = f"{len(bad)} transaction(s) missing customer_state despite master value"
        log.info("[DM-V11] %s - %s", res.status, res.message)
        return res

    # ---------- DM-V12 ----------
    def validate_net_amount(self, business_date):
        res = ValidationResult(
            test_case_id="DM-V12", layer=LAYER,
            description="net_sales_amount = gross_sales_amount - discount_amount",
            source_object="stg_* gross/discount", target_object=f"{DM}.net_sales_amount",
            expected="recomputed value within tolerance",
            severity="Critical", risk_ref="R-DM-10")
        dm = self.db.query(
            "datamart",
            "SELECT sales_transaction_id, gross_sales_amount, "
            "COALESCE(discount_amount,0) AS discount_amount, net_sales_amount "
            "FROM dm_sales_transaction WHERE sale_date = :d", {"d": business_date})
        if dm.empty:
            res.status, res.message = "SKIPPED", "No data mart rows"
            return res
        for c in ("gross_sales_amount", "discount_amount", "net_sales_amount"):
            dm[c] = pd.to_numeric(dm[c], errors="coerce").fillna(0)
        dm["_expected"] = dm["gross_sales_amount"] - dm["discount_amount"]
        bad = dm[(dm["_expected"] - dm["net_sales_amount"]).abs() > self.tolerance]
        res.actual = f"{len(bad)} mismatch(es) of {len(dm)}"
        res.status = "PASS" if bad.empty else "FAIL"
        res.message = f"{len(bad)} transaction(s) with incorrect net_sales_amount"
        res.failed_sample = bad[["sales_transaction_id", "gross_sales_amount",
                                 "discount_amount", "net_sales_amount",
                                 "_expected"]].head(10).to_dict("records")
        log.info("[DM-V12] %s - %s", res.status, res.message)
        return res

    # ---------- DM-V14 / V15 / V17 ----------
    def validate_completeness(self, business_date):
        results = []
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

        res = ValidationResult(
            test_case_id="DM-V15", layer=LAYER,
            description="Null-customer transactions must not count as valid net sales",
            source_object="stg_distributor_sales", target_object=DM,
            expected="excluded or flagged", severity="High", risk_ref="R-DM-13")
        bad = self.db.query(
            "datamart",
            "SELECT sales_transaction_id FROM dm_sales_transaction "
            "WHERE sale_date = :d AND (customer_id IS NULL OR customer_id = '') "
            "AND net_sales_amount > 0", {"d": business_date})
        res.actual = f"{len(bad)} row(s)"
        res.status = "PASS" if bad.empty else "FAIL"
        res.message = f"{len(bad)} null-customer transaction(s) contributing to net sales"
        res.failed_sample = bad["sales_transaction_id"].head(10).tolist() if not bad.empty else []
        log.info("[DM-V15] %s - %s", res.status, res.message)
        results.append(res)

        results.append(self.validate_duplicates(
            "DM-V17", LAYER, "sales_transaction_id is unique in the data mart",
            self._dm(business_date, "sales_transaction_id"), "sales_transaction_id",
            DM, "Critical", "R-DM-12"))
        return results

    # ---------- MST-V04 ----------
    def validate_kyc_compliance(self, business_date):
        res = ValidationResult(
            test_case_id="MST-V04", layer=DQ,
            description="REJECTED-KYC customers must not contribute to net sales",
            source_object="customer_master_raw.kyc_status", target_object=DM,
            expected="0 rejected-KYC contributors", severity="Critical", risk_ref="R-DQ-06")
        dm = self.db.query(
            "datamart",
            "SELECT sales_transaction_id, customer_id, net_sales_amount FROM dm_sales_transaction "
            "WHERE sale_date = :d AND customer_id IS NOT NULL", {"d": business_date})
        cm = self.db.query("source_retail",
                           "SELECT customer_id FROM customer_master_raw "
                           "WHERE UPPER(kyc_status) = 'REJECTED'")
        if dm.empty:
            res.status, res.message = "SKIPPED", "No data mart rows"
            return res
        bad = dm[dm["customer_id"].isin(cm["customer_id"])] if not cm.empty else pd.DataFrame()
        value = float(pd.to_numeric(bad["net_sales_amount"], errors="coerce").sum()) \
            if not bad.empty else 0.0
        res.actual = f"{len(bad)} txn, value {value}"
        res.status = "PASS" if bad.empty else "FAIL"
        res.message = f"{len(bad)} REJECTED-KYC transaction(s) contributing {value} to net sales"
        res.failed_sample = bad[["sales_transaction_id", "customer_id"]].head(10).to_dict("records") \
            if not bad.empty else []
        log.info("[MST-V04] %s - %s", res.status, res.message)
        return res
