"""Executes the REAL validator code paths against the seeded SQLite replica.

This is the proof the suite runs: it reports runtime errors, duplicate
validation IDs, malformed results and missing variance values. It is not a
substitute for executing against the lab environment.

    python tests/run_against_mock.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for k, v in {
    "DB_HOST": "localhost", "DB_USER": "mock", "DB_PASSWORD": "mock",
    "API_BASE_URL": "http://127.0.0.1:5001/api", "API_KEY": "mock-key",
    "REPORT_BASE_URL": "http://127.0.0.1:5002",
    "RETAIL_CSV_PATH": str(ROOT / "tests" / "mock" / "retail.csv"),
    "QA_ENVIRONMENT": "MOCK-SQLITE",
}.items():
    os.environ.setdefault(k, v)

from sqlalchemy import create_engine                                   # noqa: E402
from tests.mock.build_mock_db import build, DB, BD                     # noqa: E402
from src.connectors.db_connector import DBConnector                    # noqa: E402
from src.utils import report_generator as rpt                          # noqa: E402
from src.utils import defect_logger                                    # noqa: E402
from src.utils.result import PASS, FAIL, BLOCKED, SKIPPED              # noqa: E402


class MockDB(DBConnector):
    """Every logical schema resolves to the single SQLite file."""

    def __init__(self):
        self._dbs = {k: "mock" for k in
                     ("source_retail", "source_distributor", "source_online",
                      "staging", "datamart", "reporting")}
        self._engine_obj = create_engine(f"sqlite:///{DB}")

    def _engine(self, logical_name):
        if logical_name not in self._dbs:
            raise KeyError(f"Unknown logical database '{logical_name}'")
        return self._engine_obj

    def dispose(self):
        self._engine_obj.dispose()


class MockAPI:
    """Simulates the Online Sales API from the mock source table."""

    def __init__(self, db):
        self.db = db
        self.cfg = {"endpoint": "/online-sales", "health_endpoint": "/health"}

    class _Resp:
        def __init__(self, code):
            self.status_code = code

    def health(self):
        return self._Resp(200)

    def _get(self, path, params=None, api_key_override=None):
        return self._Resp(401 if api_key_override else 200)

    def fetch_all_pages(self, from_date, to_date, status=None):
        df = self.db.query("source_online",
                           "SELECT * FROM online_sales_raw "
                           "WHERE sale_date BETWEEN :f AND :t",
                           {"f": from_date, "t": to_date})
        return df, len(df)


class MockReports:
    """Simulates the Flask reporting pages from the reporting views."""

    VIEW_OF = {
        "executive_dashboard": "vw_executive_dashboard",
        "channel_performance": "vw_channel_performance",
        "region_performance": "vw_region_performance",
        "product_performance": "vw_product_performance",
        "daily_sales_trend": "vw_daily_sales_trend",
    }

    def __init__(self, db, drift=None):
        self.db = db
        self.drift = drift or {}

    def page_total(self, report_key, measure_hints=None, params=None):
        view = self.VIEW_OF[report_key]
        p = {"d": (params or {}).get("date", BD)}
        sql = (f"SELECT COALESCE(SUM(total_net_sales_amount),0) AS t FROM {view} "
               "WHERE sale_date = :d")
        if (params or {}).get("region"):
            sql += " AND region_name = :r"
            p["r"] = params["region"]
        val = float(self.db.query("reporting", sql, p).iloc[0]["t"])
        return round(val + self.drift.get(report_key, 0.0), 2)

    def tables(self, *a, **k):
        return []

    def extract_metrics(self, *a, **k):
        return {}


def main():
    build()
    db = MockDB()
    api = MockAPI(db)
    rc = MockReports(db, drift={"region_performance": -5000.0})   # seeded reporting drift

    from src.validators.source_to_staging import SourceToStagingValidator
    from src.validators.staging_to_datamart import StagingToDataMartValidator
    from src.validators.aggregate_validator import AggregateValidator
    from src.validators.reporting_validator import ReportingValidator

    results, errors = [], []

    def guard(label, fn):
        try:
            out = fn()
            return out if isinstance(out, list) else [out]
        except Exception as exc:
            import traceback
            errors.append((label, f"{type(exc).__name__}: {exc}",
                           traceback.format_exc(limit=6)))
            return []

    s2s = SourceToStagingValidator(db, None, api)
    results += guard("run_retail", lambda: s2s.run_retail(BD))
    results += guard("run_distributor", lambda: s2s.run_distributor(BD))
    results += guard("run_online", lambda: s2s.run_online(BD))
    results += guard("run_master", lambda: s2s.run_master(BD))

    dm = StagingToDataMartValidator(db)
    results += guard("product_harmonisation", lambda: dm.validate_product_harmonisation(BD))
    results += guard("region_mapping", lambda: dm.validate_region_mapping(BD))
    results += guard("completeness", lambda: dm.validate_completeness(BD))
    results += guard("channel_mapping", lambda: dm.validate_channel_mapping(BD))
    results += guard("customer_cleansing", lambda: dm.validate_customer_cleansing(BD))
    results += guard("customer_state", lambda: dm.validate_customer_state(BD))
    results += guard("net_amount", lambda: dm.validate_net_amount(BD))
    results += guard("kyc_compliance", lambda: dm.validate_kyc_compliance(BD))

    ag = AggregateValidator(db)
    results += guard("region_summary", lambda: ag.validate_region_summary(BD))
    results += guard("daily_summary", lambda: ag.validate_daily_summary(BD))
    results += guard("executive_summary", lambda: ag.validate_executive_summary(BD))
    results += guard("channel_summary", lambda: ag.validate_channel_summary(BD))
    results += guard("product_summary", lambda: ag.validate_product_summary(BD))
    results += guard("cross_consistency", lambda: ag.validate_cross_consistency(BD))

    rp = ReportingValidator(db, rc)
    results += guard("views", lambda: rp.validate_views(BD))
    results += guard("dashboards", lambda: rp.validate_dashboards(BD))
    results += guard("filters", lambda: rp.validate_filters(BD))
    results += guard("freshness", lambda: rp.validate_freshness(BD))
    results += guard("cross_report", lambda: rp.validate_cross_report(BD))

    db.dispose()

    print("\n" + "=" * 78)
    print("  MOCK INTEGRATION RUN")
    print("=" * 78)
    if errors:
        print(f"  RUNTIME ERRORS: {len(errors)}\n")
        for label, msg, tb in errors:
            print(f"  [{label}] {msg}")
            print("    " + tb.strip().replace("\n", "\n    ")[:900] + "\n")
    else:
        print("  RUNTIME ERRORS: 0  - every validator executed cleanly")

    counts = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    print(f"\n  Results: {len(results)}  {counts}")

    malformed = [r.test_case_id for r in results
                 if not r.test_case_id or not r.layer
                 or r.status not in (PASS, FAIL, BLOCKED, SKIPPED)]
    print(f"  Malformed results: {len(malformed)} {malformed[:5]}")
    novar = [r.test_case_id for r in results
             if r.status in (PASS, FAIL) and r.variance is None]
    print(f"  Missing variance:  {len(novar)} {novar[:5]}")
    seen = {}
    for r in results:
        seen[r.test_case_id] = seen.get(r.test_case_id, 0) + 1
    dupes = [k for k, v in seen.items() if v > 1]
    print(f"  Duplicate IDs:     {dupes or 'none'}")
    print("=" * 78 + "\n")

    if results:
        rpt.to_excel(results, "mock_validation_report.xlsx", BD)
        defect_logger.to_excel(results, "mock_defect_log.xlsx", BD)
    return 1 if errors or malformed or novar or dupes else 0


if __name__ == "__main__":
    sys.exit(main())
