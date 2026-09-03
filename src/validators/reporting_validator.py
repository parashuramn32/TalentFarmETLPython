"""Layer 3b validators - Data Mart to Reporting.

All five views are SELECT * from their data mart summary table
(04CreateReportingViews.sql), so view-vs-data-mart must reconcile exactly.
"""
import pandas as pd

from src.validators.base_validator import BaseValidator
from src.utils.config_loader import load_config, load_rules
from src.utils.result import ValidationResult
from src.utils.logger import get_logger

log = get_logger(__name__)
LAYER = "Data Mart-to-Reporting"

VIEW_TESTS = {
    "executive_dashboard": ("RPT-V01", "Critical"),
    "channel_performance": ("RPT-V02", "High"),
    "region_performance":  ("RPT-V11", "High"),
    "product_performance": ("RPT-V12", "High"),
    "daily_sales_trend":   ("RPT-V13", "High"),
}
PAGE_TESTS = {
    "executive_dashboard": ("RPT-V03", "Critical"),
    "channel_performance": ("RPT-V04", "High"),
    "region_performance":  ("RPT-V05", "High"),
    "product_performance": ("RPT-V06", "High"),
    "daily_sales_trend":   ("RPT-V07", "High"),
}


class ReportingValidator(BaseValidator):
    def __init__(self, db, report_conn):
        rules = load_rules()
        super().__init__(tolerance=rules["tolerance"]["amount"])
        self.db, self.rc, self.rules = db, report_conn, rules
        self.cfg = load_config("reporting_config")["reports"]

    def validate_views(self, business_date):
        results = []
        for key, (tc, sev) in VIEW_TESTS.items():
            spec = self.cfg[key]
            view, dm_tbl, measure = spec["view"], spec["dm_table"], spec["measure"]
            res = ValidationResult(
                test_case_id=tc, layer=LAYER,
                description=f"{view} reconciles with {dm_tbl}",
                source_object=dm_tbl, target_object=view,
                severity=sev, risk_ref="R-RP-05")
            try:
                v = self.db.scalar("reporting",
                                   f"SELECT COALESCE(SUM({measure}),0) FROM {view} "
                                   "WHERE sale_date = :d", {"d": business_date})
                d = self.db.scalar("datamart",
                                   f"SELECT COALESCE(SUM({measure}),0) FROM {dm_tbl} "
                                   "WHERE sale_date = :d", {"d": business_date})
                vr = self.db.count("reporting", view, "sale_date = :d", {"d": business_date})
                dr = self.db.count("datamart", dm_tbl, "sale_date = :d", {"d": business_date})
                res.expected = f"{dr} rows / {round(float(d or 0), 2)}"
                res.actual = f"{vr} rows / {round(float(v or 0), 2)}"
                ok = (abs(float(d or 0) - float(v or 0)) <= self.tolerance) and (vr == dr)
                res.status = "PASS" if ok else "FAIL"
                res.message = f"data mart={res.expected}, view={res.actual}"
            except Exception as exc:
                res.status, res.message = "ERROR", str(exc)[:200]
            log.info("[%s] %s - %s", tc, res.status, res.message)
            results.append(res)
        return results

    def validate_dashboards(self, business_date):
        results = []
        for key, (tc, sev) in PAGE_TESTS.items():
            spec = self.cfg[key]
            view, measure, path = spec["view"], spec["measure"], spec["path"]
            res = ValidationResult(
                test_case_id=tc, layer=LAYER,
                description=f"{path} displayed total vs {view}",
                source_object=view, target_object=path,
                severity=sev, risk_ref="R-RP-01")
            try:
                expected = float(self.db.scalar(
                    "reporting", f"SELECT COALESCE(SUM({measure}),0) FROM {view} "
                    "WHERE sale_date = :d", {"d": business_date}) or 0)
                displayed = self.rc.page_total(key, params={"date": business_date})
                res.expected, res.actual = round(expected, 2), displayed
                if displayed is None:
                    res.status, res.message = "FAIL", "Could not extract a total from the page"
                else:
                    ok = abs(expected - displayed) <= max(self.tolerance, 1.0)
                    res.status = "PASS" if ok else "FAIL"
                    res.message = f"view={res.expected}, displayed={displayed}"
            except Exception as exc:
                res.status, res.message = "ERROR", str(exc)[:200]
            log.info("[%s] %s - %s", tc, res.status, res.message)
            results.append(res)
        return results

    def validate_filters(self, business_date):
        res = ValidationResult(
            test_case_id="RPT-V08", layer=LAYER,
            description="Region filter on the region report matches filtered data mart query",
            source_object="vw_region_performance",
            target_object=self.cfg["region_performance"]["path"],
            severity="High", risk_ref="R-RP-02")
        try:
            measure = self.cfg["region_performance"]["measure"]
            regions = self.db.query(
                "reporting",
                "SELECT DISTINCT region_name FROM vw_region_performance WHERE sale_date = :d",
                {"d": business_date})
            if regions.empty:
                res.status, res.message = "SKIPPED", "No regions available to filter"
                return res
            region = regions.iloc[0, 0]
            expected = float(self.db.scalar(
                "reporting", f"SELECT COALESCE(SUM({measure}),0) FROM vw_region_performance "
                "WHERE sale_date = :d AND region_name = :r",
                {"d": business_date, "r": region}) or 0)
            displayed = self.rc.page_total("region_performance",
                                           params={"date": business_date, "region": region})
            res.expected, res.actual = round(expected, 2), displayed
            if displayed is None:
                res.status, res.message = "FAIL", f"No filtered total extracted for {region}"
            else:
                ok = abs(expected - displayed) <= max(self.tolerance, 1.0)
                res.status = "PASS" if ok else "FAIL"
                res.message = f"region={region}: expected={res.expected}, displayed={displayed}"
        except Exception as exc:
            res.status, res.message = "ERROR", str(exc)[:200]
        log.info("[RPT-V08] %s - %s", res.status, res.message)
        return res

    def validate_freshness(self, business_date):
        res = ValidationResult(
            test_case_id="RPT-V09", layer=LAYER,
            description="Reporting layer reflects the latest completed load",
            source_object="dm_sales_transaction", target_object="vw_daily_sales_trend",
            severity="Medium", risk_ref="R-RP-04")
        try:
            dm_max = self.db.scalar("datamart", "SELECT MAX(sale_date) FROM dm_sales_transaction")
            rp_max = self.db.scalar("reporting", "SELECT MAX(sale_date) FROM vw_daily_sales_trend")
            res.expected, res.actual = str(dm_max), str(rp_max)
            res.status = "PASS" if str(dm_max) == str(rp_max) else "FAIL"
            res.message = f"data mart max date={dm_max}, reporting max date={rp_max}"
        except Exception as exc:
            res.status, res.message = "ERROR", str(exc)[:200]
        log.info("[RPT-V09] %s - %s", res.status, res.message)
        return res

    def validate_cross_report(self, business_date):
        res = ValidationResult(
            test_case_id="RPT-V10", layer=LAYER,
            description="Executive net total equals channel, region and daily view totals",
            source_object="vw_executive_dashboard",
            target_object="vw_channel_performance / vw_region_performance / vw_daily_sales_trend",
            severity="High", risk_ref="R-RP-06")
        try:
            totals = {}
            for name, view in (("executive", "vw_executive_dashboard"),
                               ("channel", "vw_channel_performance"),
                               ("region", "vw_region_performance"),
                               ("daily", "vw_daily_sales_trend")):
                v = self.db.scalar(
                    "reporting",
                    f"SELECT COALESCE(SUM(total_net_sales_amount),0) FROM {view} "
                    "WHERE sale_date = :d", {"d": business_date})
                totals[name] = round(float(v or 0), 2)
            res.expected, res.actual = totals.get("executive"), totals
            res.status = "PASS" if len(set(totals.values())) <= 1 else "FAIL"
            res.message = f"view totals: {totals}"
        except Exception as exc:
            res.status, res.message = "ERROR", str(exc)[:200]
        log.info("[RPT-V10] %s - %s", res.status, res.message)
        return res
