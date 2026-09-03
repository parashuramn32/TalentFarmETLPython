"""Layer 3a - Aggregate / summary model correctness.

Column contract (02CreateTables.sql):
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
        self.avg_tol = rules["tolerance"].get("average", 0.5)

    def _q(self, alias, sql, params=None):
        try:
            return self.db.query(alias, sql, params)
        except Exception as exc:
            log.error("Query failed on %s: %s", alias, exc)
            return None

    def _compare_groups(self, tc, desc, expected_df, actual_df, keys, measures,
                        target_object, severity, risk_ref):
        res = self._res(tc, LAYER, desc, source_object=DM, target_object=target_object,
                        expected=0, severity=severity, risk_ref=risk_ref)
        if expected_df is None or actual_df is None:
            return self._blocked(res, "Transaction detail or summary table could not be read")
        if expected_df.empty and actual_df.empty:
            return self._skipped(res, "No rows for the business date")
        exp, act = expected_df.copy(), actual_df.copy()
        for df in (exp, act):
            for k in keys:
                if k in df.columns:
                    df[k] = df[k].astype(str)
        m = exp.merge(act, on=keys, how="outer", suffixes=("_exp", "_act"), indicator=True)
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
        res.actual = sum(v for v in breaches.values() if isinstance(v, int))
        res.failed_sample = list(breaches.items())[:10]
        res.status = "PASS" if not breaches else "FAIL"
        res.message = f"{len(m)} group(s) compared; breaches: {breaches or 'none'}"
        res.compute_variance()
        log.info("[%s] %s - %s", tc, res.status, res.message)
        return res

    def _avg_check(self, tc, act, obj):
        net, cnt, avg = self.s["net"], self.s["count"], self.s["avg"]
        res = self._res(tc, LAYER, f"{avg} equals {net} divided by {cnt} (zero-count safe)",
                        source_object=obj, target_object=f"{obj}.{avg}",
                        expected=0, severity="High", risk_ref="R-AG-03")
        if act is None:
            return self._blocked(res, "Summary table could not be read")
        if act.empty:
            return self._skipped(res, "No summary rows for the business date")
        if avg not in act.columns:
            return self._blocked(res, f"Column '{avg}' absent from {obj}")
        a = act.copy()
        for c in (net, cnt, avg):
            a[c] = pd.to_numeric(a[c], errors="coerce").fillna(0)
        a["_exp"] = a.apply(lambda r: 0 if r[cnt] == 0 else r[net] / r[cnt], axis=1)
        bad = a[(a["_exp"] - a[avg]).abs() > self.avg_tol]
        zero_div = a[a[cnt] == 0]
        res.actual = len(bad)
        res.status = "PASS" if bad.empty else "FAIL"
        res.message = (f"{len(bad)} of {len(a)} incorrect average(s); "
                       f"{len(zero_div)} zero-count group(s) handled")
        res.compute_variance()
        log.info("[%s] %s - %s", tc, res.status, res.message)
        return res

    def validate_region_summary(self, business_date):
        net, gross, cnt, avg = self.s["net"], self.s["gross"], self.s["count"], self.s["avg"]
        exp = self._q("datamart", f"""
            SELECT sale_date, region_name, standard_product_type,
                   COUNT(*) AS {cnt}, SUM(gross_sales_amount) AS {gross},
                   SUM(net_sales_amount) AS {net}
            FROM {DM} WHERE sale_date = :d
            GROUP BY sale_date, region_name, standard_product_type
        """, {"d": business_date})
        act = self._q("datamart", f"""
            SELECT sale_date, region_name, standard_product_type, {cnt}, {gross}, {net}, {avg}
            FROM dm_sales_region_summary WHERE sale_date = :d
        """, {"d": business_date})
        return [self._compare_groups(
                    "AGG-V01", "Region summary sums and counts vs transaction level",
                    exp, act, ["sale_date", "region_name", "standard_product_type"],
                    [cnt, gross, net], "dm_sales_region_summary", "Critical", "R-AG-01"),
                self._avg_check("AGG-V02", act, "dm_sales_region_summary")]

    def validate_channel_summary(self, business_date):
        net, gross, cnt = self.s["net"], self.s["gross"], self.s["count"]
        exp = self._q("datamart", f"""
            SELECT sale_date, source_channel, standard_product_type,
                   COUNT(*) AS {cnt}, SUM(gross_sales_amount) AS {gross},
                   SUM(net_sales_amount) AS {net}
            FROM {DM} WHERE sale_date = :d
            GROUP BY sale_date, source_channel, standard_product_type
        """, {"d": business_date})
        act = self._q("datamart", f"""
            SELECT sale_date, source_channel, standard_product_type, {cnt}, {gross}, {net}
            FROM dm_sales_channel_summary WHERE sale_date = :d
        """, {"d": business_date})
        return self._compare_groups(
            "AGG-V03", "Channel summary sums and counts vs transaction level",
            exp, act, ["sale_date", "source_channel", "standard_product_type"],
            [cnt, gross, net], "dm_sales_channel_summary", "Critical", "R-AG-01")

    def validate_product_summary(self, business_date):
        net, gross, cnt = self.s["net"], self.s["gross"], self.s["count"]
        exp = self._q("datamart", f"""
            SELECT sale_date, product_code, standard_product_type,
                   COUNT(*) AS {cnt}, SUM(gross_sales_amount) AS {gross},
                   SUM(net_sales_amount) AS {net}
            FROM {DM} WHERE sale_date = :d
            GROUP BY sale_date, product_code, standard_product_type
        """, {"d": business_date})
        act = self._q("datamart", f"""
            SELECT sale_date, product_code, standard_product_type, {cnt}, {gross}, {net}
            FROM dm_sales_product_summary WHERE sale_date = :d
        """, {"d": business_date})
        return self._compare_groups(
            "AGG-V04", "Product summary grouping and totals vs transaction level",
            exp, act, ["sale_date", "product_code", "standard_product_type"],
            [cnt, gross, net], "dm_sales_product_summary", "High", "R-AG-02")

    def validate_daily_summary(self, business_date):
        net, gross, cnt = self.s["net"], self.s["gross"], self.s["count"]
        exp = self._q("datamart", f"""
            SELECT sale_date, COUNT(*) AS {cnt},
                   SUM(gross_sales_amount) AS {gross}, SUM(net_sales_amount) AS {net},
                   SUM(CASE WHEN standard_product_type='INSURANCE'
                        THEN net_sales_amount ELSE 0 END) AS total_insurance_premium,
                   SUM(CASE WHEN standard_product_type='MUTUAL_FUND'
                        THEN net_sales_amount ELSE 0 END) AS total_mutual_fund_sales
            FROM {DM} WHERE sale_date = :d GROUP BY sale_date
        """, {"d": business_date})
        act = self._q("datamart", f"""
            SELECT sale_date, {cnt}, {gross}, {net},
                   total_insurance_premium, total_mutual_fund_sales, avg_ticket_size
            FROM dm_sales_daily_summary WHERE sale_date = :d
        """, {"d": business_date})
        return [self._compare_groups(
                    "AGG-V05", "Daily summary totals and product split vs transaction level",
                    exp, act, ["sale_date"],
                    [cnt, gross, net, "total_insurance_premium", "total_mutual_fund_sales"],
                    "dm_sales_daily_summary", "High", "R-AG-02"),
                self._avg_check("AGG-V09", act, "dm_sales_daily_summary")]

    def validate_executive_summary(self, business_date):
        """Each executive metric gets its own validation ID (AGG-V06a..e) so that
        every reported row is individually traceable (Assignment 3, Section 5)."""
        results = []
        e = self.e
        exp = self._q("datamart", f"""
            SELECT COUNT(*) AS {e['count']},
                   COALESCE(SUM(CASE WHEN standard_product_type='INSURANCE'
                        THEN net_sales_amount ELSE 0 END),0) AS {e['insurance']},
                   COALESCE(SUM(CASE WHEN standard_product_type='MUTUAL_FUND'
                        THEN net_sales_amount ELSE 0 END),0) AS {e['mutual_fund']},
                   COALESCE(SUM(net_sales_amount),0) AS {e['net']}
            FROM {DM} WHERE sale_date = :d
        """, {"d": business_date})
        act = self._q("datamart", f"""
            SELECT {e['count']}, {e['insurance']}, {e['mutual_fund']}, {e['net']},
                   {e['avg']}, {e['top_region']}, {e['top_channel']}
            FROM dm_executive_sales_summary WHERE sale_date = :d
        """, {"d": business_date})

        base = self._res("AGG-V06a", LAYER, "Executive summary metrics vs recomputed values",
                         source_object=DM, target_object="dm_executive_sales_summary",
                         severity="Critical", risk_ref="R-AG-04")
        if exp is None or act is None:
            return [self._blocked(base, "Data mart or executive summary could not be read")]
        if act.empty:
            return [self._skipped(base, "No executive summary row for the business date")]

        ex, ac = exp.iloc[0], act.iloc[0]
        for tc, metric, sev in (("AGG-V06a", e["net"], "Critical"),
                                ("AGG-V06b", e["insurance"], "Critical"),
                                ("AGG-V06c", e["mutual_fund"], "Critical"),
                                ("AGG-V06d", e["count"], "High")):
            results.append(self.validate_numeric(
                tc, LAYER, f"Executive metric '{metric}' matches the recomputed value",
                float(ex[metric]), float(ac[metric]), DM,
                "dm_executive_sales_summary", sev, "R-AG-04"))

        exp_avg = 0 if int(ex[e["count"]]) == 0 else float(ex[e["net"]]) / int(ex[e["count"]])
        results.append(self.validate_numeric(
            "AGG-V06e", LAYER, f"Executive '{e['avg']}' matches the recomputed value",
            round(exp_avg, 2), float(ac[e["avg"]]), DM,
            "dm_executive_sales_summary", "High", "R-AG-04", tolerance=self.avg_tol))

        res = self._res("AGG-V07", LAYER, "top_region and top_channel derived correctly",
                        source_object=DM, target_object="dm_executive_sales_summary",
                        severity="Medium", risk_ref="R-AG-05")
        tr = self._q("datamart", f"""
            SELECT region_name FROM {DM} WHERE sale_date = :d
            GROUP BY region_name ORDER BY SUM(net_sales_amount) DESC LIMIT 1""",
            {"d": business_date})
        tc_ = self._q("datamart", f"""
            SELECT source_channel FROM {DM} WHERE sale_date = :d
            GROUP BY source_channel ORDER BY SUM(net_sales_amount) DESC LIMIT 1""",
            {"d": business_date})
        if tr is None or tc_ is None:
            results.append(self._blocked(res, "Data mart could not be read"))
        else:
            exp_r = tr.iloc[0, 0] if not tr.empty else None
            exp_c = tc_.iloc[0, 0] if not tc_.empty else None
            ok = (str(exp_r).upper() == str(ac[e["top_region"]]).upper() and
                  str(exp_c).upper() == str(ac[e["top_channel"]]).upper())
            res.expected = f"region={exp_r}, channel={exp_c}"
            res.actual = f"region={ac[e['top_region']]}, channel={ac[e['top_channel']]}"
            res.status = "PASS" if ok else "FAIL"
            res.message = f"expected {res.expected}; actual {res.actual}"
            res.compute_variance()
            log.info("[AGG-V07] %s - %s", res.status, res.message)
            results.append(res)
        return results

    def validate_cross_consistency(self, business_date):
        net = self.s["net"]
        res = self._res("AGG-V08", LAYER,
                        "All summary models reconcile to the same daily net total",
                        source_object=DM, target_object="dm_* summary models",
                        severity="High", risk_ref="R-AG-06")
        totals, failed = {}, False
        for name, tbl, col in (("region", "dm_sales_region_summary", net),
                               ("channel", "dm_sales_channel_summary", net),
                               ("product", "dm_sales_product_summary", net),
                               ("daily", "dm_sales_daily_summary", net),
                               ("executive", "dm_executive_sales_summary", self.e["net"])):
            v = self._q("datamart",
                        f"SELECT COALESCE(SUM({col}),0) AS t FROM {tbl} WHERE sale_date = :d",
                        {"d": business_date})
            if v is None:
                failed, totals[name] = True, "unavailable"
            else:
                totals[name] = round(float(v.iloc[0]["t"] or 0), 2)
        if failed:
            return self._blocked(res, f"One or more summary tables unreadable: {totals}")
        numeric = [v for v in totals.values() if isinstance(v, float)]
        res.expected = numeric[0] if numeric else None
        res.actual = totals
        res.status = "PASS" if len(set(numeric)) <= 1 else "FAIL"
        res.message = f"summary totals: {totals}"
        res.compute_variance()
        log.info("[AGG-V08] %s - %s", res.status, res.message)
        return res
