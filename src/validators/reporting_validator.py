"""Layer 3b - Data Mart to Reporting.

All five views are SELECT * of their data mart summary table
(04CreateReportingViews.sql), so view-vs-data-mart must reconcile exactly.
"""
from src.validators.base_validator import BaseValidator
from src.utils.config_loader import load_config, load_rules
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
        # Displayed figures are rounded for presentation, so allow a wider band.
        self.display_tol = max(self.tolerance, 1.0)

    def validate_views(self, business_date):
        results = []
        for key, (tc, sev) in VIEW_TESTS.items():
            spec = self.cfg[key]
            view, dm_tbl, measure = spec["view"], spec["dm_table"], spec["measure"]
            res = self._res(tc, LAYER, f"{view} reconciles with {dm_tbl}",
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
            except Exception as exc:
                results.append(self._blocked(
                    res, f"View or data mart unreadable: {type(exc).__name__}"))
                continue
            if dr == 0 and vr == 0:
                results.append(self._skipped(res, "No rows in either object for the date"))
                continue
            res.expected, res.actual = round(float(d or 0), 2), round(float(v or 0), 2)
            ok = (abs(float(d or 0) - float(v or 0)) <= self.tolerance) and (vr == dr)
            res.status = "PASS" if ok else "FAIL"
            res.message = (f"data mart: {dr} row(s) / {res.expected} | "
                           f"view: {vr} row(s) / {res.actual}")
            res.compute_variance()
            log.info("[%s] %s - %s", tc, res.status, res.message)
            results.append(res)
        return results

    def validate_dashboards(self, business_date):
        results = []
        for key, (tc, sev) in PAGE_TESTS.items():
            spec = self.cfg[key]
            view, measure, path = spec["view"], spec["measure"], spec["path"]
            res = self._res(tc, LAYER, f"{path} displayed total vs {view}",
                            source_object=view, target_object=path,
                            severity=sev, risk_ref="R-RP-01")
            try:
                expected = float(self.db.scalar(
                    "reporting", f"SELECT COALESCE(SUM({measure}),0) FROM {view} "
                    "WHERE sale_date = :d", {"d": business_date}) or 0)
            except Exception as exc:
                results.append(self._blocked(
                    res, f"Reporting view unreadable: {type(exc).__name__}"))
                continue
            try:
                displayed = self.rc.page_total(key, params={"date": business_date})
            except Exception as exc:
                results.append(self._blocked(
                    res, f"Report page unreachable: {type(exc).__name__}"))
                continue
            if displayed is None:
                results.append(self._blocked(
                    res, "No numeric total could be extracted from the rendered page"))
                continue
            res.expected, res.actual = round(expected, 2), displayed
            res.status = "PASS" if abs(expected - displayed) <= self.display_tol else "FAIL"
            res.message = f"view={res.expected}, displayed={displayed}"
            res.compute_variance()
            log.info("[%s] %s - %s", tc, res.status, res.message)
            results.append(res)
        return results

    def validate_filters(self, business_date):
        res = self._res("RPT-V08", LAYER,
                        "Region filter on the region report matches the filtered data mart query",
                        source_object="vw_region_performance",
                        target_object=self.cfg["region_performance"]["path"],
                        severity="High", risk_ref="R-RP-02")
        measure = self.cfg["region_performance"]["measure"]
        try:
            regions = self.db.query(
                "reporting",
                "SELECT DISTINCT region_name FROM vw_region_performance WHERE sale_date = :d",
                {"d": business_date})
        except Exception as exc:
            return self._blocked(res, f"Reporting view unreadable: {type(exc).__name__}")
        if regions.empty:
            return self._skipped(res, "No regions available to filter on")
        region = regions.iloc[0, 0]
        try:
            expected = float(self.db.scalar(
                "reporting", f"SELECT COALESCE(SUM({measure}),0) FROM vw_region_performance "
                "WHERE sale_date = :d AND region_name = :r",
                {"d": business_date, "r": region}) or 0)
            displayed = self.rc.page_total("region_performance",
                                           params={"date": business_date, "region": region})
        except Exception as exc:
            return self._blocked(res, f"Filtered report unavailable: {type(exc).__name__}")
        if displayed is None:
            return self._blocked(res, f"No filtered total extracted for region {region}")
        res.expected, res.actual = round(expected, 2), displayed
        res.status = "PASS" if abs(expected - displayed) <= self.display_tol else "FAIL"
        res.message = f"region={region}: expected={res.expected}, displayed={displayed}"
        res.compute_variance()
        log.info("[RPT-V08] %s - %s", res.status, res.message)
        return res

    def validate_freshness(self, business_date):
        res = self._res("RPT-V09", LAYER,
                        "Reporting layer reflects the latest completed load",
                        source_object="dm_sales_transaction",
                        target_object="vw_daily_sales_trend",
                        severity="Medium", risk_ref="R-RP-04")
        try:
            dm_max = self.db.scalar("datamart", "SELECT MAX(sale_date) FROM dm_sales_transaction")
            rp_max = self.db.scalar("reporting", "SELECT MAX(sale_date) FROM vw_daily_sales_trend")
        except Exception as exc:
            return self._blocked(res, f"Could not read max sale_date: {type(exc).__name__}")
        res.expected, res.actual = str(dm_max), str(rp_max)
        res.status = "PASS" if str(dm_max) == str(rp_max) else "FAIL"
        res.message = f"data mart max date={dm_max}, reporting max date={rp_max}"
        res.compute_variance()
        log.info("[RPT-V09] %s - %s", res.status, res.message)
        return res

    def validate_cross_report(self, business_date):
        res = self._res("RPT-V10", LAYER,
                        "Executive net total equals the channel, region and daily view totals",
                        source_object="vw_executive_dashboard",
                        target_object="vw_channel_performance / vw_region_performance / "
                                      "vw_daily_sales_trend",
                        severity="High", risk_ref="R-RP-06")
        totals = {}
        for name, view in (("executive", "vw_executive_dashboard"),
                           ("channel", "vw_channel_performance"),
                           ("region", "vw_region_performance"),
                           ("daily", "vw_daily_sales_trend")):
            try:
                v = self.db.scalar(
                    "reporting",
                    f"SELECT COALESCE(SUM(total_net_sales_amount),0) FROM {view} "
                    "WHERE sale_date = :d", {"d": business_date})
                totals[name] = round(float(v or 0), 2)
            except Exception as exc:
                return self._blocked(res, f"{view} unreadable: {type(exc).__name__}")
        res.expected, res.actual = totals.get("executive"), totals
        res.status = "PASS" if len(set(totals.values())) <= 1 else "FAIL"
        res.message = f"view totals: {totals}"
        res.compute_variance()
        log.info("[RPT-V10] %s - %s", res.status, res.message)
        return res
