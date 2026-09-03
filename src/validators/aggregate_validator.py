"""Layer 3a validators - Aggregate / summary model correctness.

Summary column contract (02CreateTables.sql):
  dm_sales_daily_summary    (sale_date PK, transaction_count, total_gross_sales_amount,
                             total_net_sales_amount, total_insurance_premium,
                             total_mutual_fund_sales, avg_ticket_size)
  dm_sales_channel_summary  (sale_date, source_channel, standard_product_type, ...)
  dm_sales_region_summary   (sale_date, region_name, standard_product_type, ...)
  dm_sales_product_summary  (sale_date, product_code, standard_product_name,
                             standard_product_type, product_category, ...)
  dm_executive_sales_summary(sale_date PK, total_transactions, total_insurance_premium,
                             total_mutual_fund_sales, total_net_sales_amount,
                             avg_ticket_size, top_region, top_channel)
"""
import pandas as pd

from src.validators.base_validator import BaseValidator
from src.utils.config_loader import load_rules
from src.utils.result import ValidationResult
from src.utils.logger import get_logger

log = get_logger(__name__)
LAYER = "Aggregate Model"
DM = "dm_sales_transaction"


class AggregateValidator(BaseValidator):
    def __init__(self, db):
        rules = load_rules()
        super().__init__(tolerance=rules["tolerance"]["amount"])
        self.db, self.rules = db, rules
        self.s = rules["summary_columns"]
        self.e = rules["executive_columns"]

    def _compare_groups(self, tc, desc, expected_df, actual_df, keys, measures,
                        target_object, severity, risk_ref):
        res = ValidationResult(
            test_case_id=tc, layer=LAYER, description=desc,
            source_object=DM, target_object=target_object,
            expected="summary equals recomputed transaction-level values",
            severity=severity, risk_ref=risk_ref)
        if expected_df.empty or actual_df.empty:
            res.status, res.message = "SKIPPED", "No rows to compare"
            return res
        for df in (expected_df, actual_df):
            for k in keys:
                if k in df.columns:
                    df[k] = df[k].astype(str)
        m = expected_df.merge(actual_df, on=keys, how="outer",
                              suffixes=("_exp", "_act"), indicator=True)
        breaches = {}
        only = m[m["_merge"] != "both"]
        if not only.empty:
            breaches["missing_or_extra_groups"] = len(only)
        for meas in measures:
            ce, ca = f"{meas}_exp", f"{meas}_act"
            if ce not in m.columns or ca not in m.columns:
                continue
            e = pd.to_numeric(m[ce], errors="coerce").fillna(0)
            a = pd.to_numeric(m[ca], errors="coerce").fillna(0)
            bad = m[(e - a).abs() > self.tolerance]
            if not bad.empty:
                breaches[meas] = len(bad)
        res.actual = breaches or "all groups and measures match"
        res.status = "PASS" if not breaches else "FAIL"
        res.message = f"aggregate breaches: {breaches or 'none'}"
        res.failed_sample = list(breaches.items())[:10]
        log.info("[%s] %s - %s", tc, res.status, res.message)
        return res

    def _avg_check(self, tc, act, obj):
        net, cnt, avg = self.s["net"], self.s["count"], self.s["avg"]
        res = ValidationResult(
            test_case_id=tc, layer=LAYER,
            description=f"{avg} = {net} / {cnt} (zero-count safe)",
            source_object=obj, target_object=f"{obj}.{avg}",
            expected="recomputed average", severity="High", risk_ref="R-AG-03")
        if act.empty or avg not in act.columns:
            res.status, res.message = "SKIPPED", f"No {avg} column"
            return res
        a = act.copy()
        for c in (net, cnt, avg):
            a[c] = pd.to_numeric(a[c], errors="coerce").fillna(0)
        a["_exp"] = a.apply(lambda r: 0 if r[cnt] == 0 else r[net] / r[cnt], axis=1)
        bad = a[(a["_exp"] - a[avg]).abs() > max(self.tolerance, 0.5)]
        zero_div = a[a[cnt] == 0]
        res.actual = f"{len(bad)} mismatch(es), {len(zero_div)} zero-count group(s)"
        res.status = "PASS" if bad.empty else "FAIL"
        res.message = f"{len(bad)} incorrect average(s); {len(zero_div)} zero-count group(s) handled"
        log.info("[%s] %s - %s", tc, res.status, res.message)
        return res

    def validate_region_summary(self, business_date):
        net, gross, cnt, avg = self.s["net"], self.s["gross"], self.s["count"], self.s["avg"]
        exp = self.db.query("datamart", f"""
            SELECT sale_date, region_name, standard_product_type,
                   COUNT(*) AS {cnt}, SUM(gross_sales_amount) AS {gross},
                   SUM(net_sales_amount) AS {net}
            FROM {DM} WHERE sale_date = :d
            GROUP BY sale_date, region_name, standard_product_type
        """, {"d": business_date})
        act = self.db.query("datamart", f"""
            SELECT sale_date, region_name, standard_product_type, {cnt}, {gross}, {net}, {avg}
            FROM dm_sales_region_summary WHERE sale_date = :d
        """, {"d": business_date})
        return [self._compare_groups(
                    "AGG-V01", "Region summary sums and counts vs transaction level",
                    exp.copy(), act.copy(),
                    ["sale_date", "region_name", "standard_product_type"],
                    [cnt, gross, net], "dm_sales_region_summary", "Critical", "R-AG-01"),
                self._avg_check("AGG-V02", act, "dm_sales_region_summary")]

    def validate_channel_summary(self, business_date):
        net, gross, cnt = self.s["net"], self.s["gross"], self.s["count"]
        exp = self.db.query("datamart", f"""
            SELECT sale_date, source_channel, standard_product_type,
                   COUNT(*) AS {cnt}, SUM(gross_sales_amount) AS {gross},
                   SUM(net_sales_amount) AS {net}
            FROM {DM} WHERE sale_date = :d
            GROUP BY sale_date, source_channel, standard_product_type
        """, {"d": business_date})
        act = self.db.query("datamart", f"""
            SELECT sale_date, source_channel, standard_product_type, {cnt}, {gross}, {net}
            FROM dm_sales_channel_summary WHERE sale_date = :d
        """, {"d": business_date})
        return self._compare_groups(
            "AGG-V03", "Channel summary sums and counts vs transaction level",
            exp, act, ["sale_date", "source_channel", "standard_product_type"],
            [cnt, gross, net], "dm_sales_channel_summary", "Critical", "R-AG-01")

    def validate_product_summary(self, business_date):
        net, gross, cnt = self.s["net"], self.s["gross"], self.s["count"]
        exp = self.db.query("datamart", f"""
            SELECT sale_date, product_code, standard_product_type,
                   COUNT(*) AS {cnt}, SUM(gross_sales_amount) AS {gross},
                   SUM(net_sales_amount) AS {net}
            FROM {DM} WHERE sale_date = :d
            GROUP BY sale_date, product_code, standard_product_type
        """, {"d": business_date})
        act = self.db.query("datamart", f"""
            SELECT sale_date, product_code, standard_product_type, {cnt}, {gross}, {net}
            FROM dm_sales_product_summary WHERE sale_date = :d
        """, {"d": business_date})
        return self._compare_groups(
            "AGG-V04", "Product summary grouping and totals vs transaction level",
            exp, act, ["sale_date", "product_code", "standard_product_type"],
            [cnt, gross, net], "dm_sales_product_summary", "High", "R-AG-02")

    def validate_daily_summary(self, business_date):
        net, gross, cnt = self.s["net"], self.s["gross"], self.s["count"]
        exp = self.db.query("datamart", f"""
            SELECT sale_date, COUNT(*) AS {cnt},
                   SUM(gross_sales_amount) AS {gross}, SUM(net_sales_amount) AS {net},
                   SUM(CASE WHEN standard_product_type='INSURANCE'
                        THEN net_sales_amount ELSE 0 END) AS total_insurance_premium,
                   SUM(CASE WHEN standard_product_type='MUTUAL_FUND'
                        THEN net_sales_amount ELSE 0 END) AS total_mutual_fund_sales
            FROM {DM} WHERE sale_date = :d GROUP BY sale_date
        """, {"d": business_date})
        act = self.db.query("datamart", f"""
            SELECT sale_date, {cnt}, {gross}, {net},
                   total_insurance_premium, total_mutual_fund_sales, avg_ticket_size
            FROM dm_sales_daily_summary WHERE sale_date = :d
        """, {"d": business_date})
        return [self._compare_groups(
                    "AGG-V05", "Daily summary totals and product split vs transaction level",
                    exp.copy(), act.copy(), ["sale_date"],
                    [cnt, gross, net, "total_insurance_premium", "total_mutual_fund_sales"],
                    "dm_sales_daily_summary", "High", "R-AG-02"),
                self._avg_check("AGG-V09", act, "dm_sales_daily_summary")]

    def validate_executive_summary(self, business_date):
        results = []
        e = self.e
        exp = self.db.query("datamart", f"""
            SELECT COUNT(*) AS {e['count']},
                   COALESCE(SUM(CASE WHEN standard_product_type='INSURANCE'
                        THEN net_sales_amount ELSE 0 END),0) AS {e['insurance']},
                   COALESCE(SUM(CASE WHEN standard_product_type='MUTUAL_FUND'
                        THEN net_sales_amount ELSE 0 END),0) AS {e['mutual_fund']},
                   COALESCE(SUM(net_sales_amount),0) AS {e['net']}
            FROM {DM} WHERE sale_date = :d
        """, {"d": business_date})
        act = self.db.query("datamart", f"""
            SELECT {e['count']}, {e['insurance']}, {e['mutual_fund']}, {e['net']},
                   {e['avg']}, {e['top_region']}, {e['top_channel']}
            FROM dm_executive_sales_summary WHERE sale_date = :d
        """, {"d": business_date})

        if exp.empty or act.empty:
            r = ValidationResult(
                test_case_id="AGG-V06", layer=LAYER,
                description="Executive summary metrics vs recomputed values",
                target_object="dm_executive_sales_summary",
                severity="Critical", risk_ref="R-AG-04")
            r.status, r.message = "SKIPPED", "No executive summary rows"
            return [r]

        ex, ac = exp.iloc[0], act.iloc[0]
        for metric, sev in ((e["net"], "Critical"), (e["insurance"], "Critical"),
                            (e["mutual_fund"], "Critical"), (e["count"], "High")):
            results.append(self.validate_numeric(
                "AGG-V06", LAYER, f"Executive metric '{metric}' matches recomputed value",
                float(ex[metric]), float(ac[metric]), DM,
                "dm_executive_sales_summary", sev, "R-AG-04"))

        exp_avg = 0 if int(ex[e["count"]]) == 0 else float(ex[e["net"]]) / int(ex[e["count"]])
        results.append(self.validate_numeric(
            "AGG-V06", LAYER, f"Executive '{e['avg']}' matches recomputed value",
            round(exp_avg, 2), float(ac[e["avg"]]), DM,
            "dm_executive_sales_summary", "High", "R-AG-04", tolerance=0.5))

        res = ValidationResult(
            test_case_id="AGG-V07", layer=LAYER,
            description="top_region and top_channel derived correctly",
            source_object=DM, target_object="dm_executive_sales_summary",
            severity="Medium", risk_ref="R-AG-05")
        tr = self.db.query("datamart", f"""
            SELECT region_name FROM {DM} WHERE sale_date = :d
            GROUP BY region_name ORDER BY SUM(net_sales_amount) DESC LIMIT 1""",
            {"d": business_date})
        tc_ = self.db.query("datamart", f"""
            SELECT source_channel FROM {DM} WHERE sale_date = :d
            GROUP BY source_channel ORDER BY SUM(net_sales_amount) DESC LIMIT 1""",
            {"d": business_date})
        exp_r = tr.iloc[0, 0] if not tr.empty else None
        exp_c = tc_.iloc[0, 0] if not tc_.empty else None
        ok = (str(exp_r).upper() == str(ac[e["top_region"]]).upper() and
              str(exp_c).upper() == str(ac[e["top_channel"]]).upper())
        res.expected = f"region={exp_r}, channel={exp_c}"
        res.actual = f"region={ac[e['top_region']]}, channel={ac[e['top_channel']]}"
        res.status = "PASS" if ok else "FAIL"
        res.message = f"expected {res.expected}; actual {res.actual}"
        log.info("[AGG-V07] %s - %s", res.status, res.message)
        results.append(res)
        return results

    def validate_cross_consistency(self, business_date):
        net = self.s["net"]
        res = ValidationResult(
            test_case_id="AGG-V08", layer=LAYER,
            description="All summary models reconcile to the same daily net total",
            source_object=DM, target_object="dm_* summary models",
            expected="identical totals", severity="High", risk_ref="R-AG-06")
        totals = {}
        for name, tbl, col in (("region", "dm_sales_region_summary", net),
                               ("channel", "dm_sales_channel_summary", net),
                               ("product", "dm_sales_product_summary", net),
                               ("daily", "dm_sales_daily_summary", net),
                               ("executive", "dm_executive_sales_summary", self.e["net"])):
            try:
                v = self.db.scalar("datamart",
                                   f"SELECT COALESCE(SUM({col}),0) FROM {tbl} WHERE sale_date = :d",
                                   {"d": business_date})
                totals[name] = round(float(v or 0), 2)
            except Exception as exc:
                totals[name] = f"ERROR: {type(exc).__name__}"
        numeric = [v for v in totals.values() if isinstance(v, float)]
        res.actual = totals
        res.status = "PASS" if len(set(numeric)) <= 1 else "FAIL"
        res.message = f"summary totals: {totals}"
        log.info("[AGG-V08] %s - %s", res.status, res.message)
        return res
