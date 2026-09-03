"""
Python QA Automation - Financial Services Sales Data Pipeline
Submission 2 : Test Design and Automation Build

Usage:
    python -m src.main --health-check
    python -m src.main --date 2026-05-01
    python -m src.main --date 2026-05-01 --layer source --layer datamart
    python -m src.main --date 2026-05-01 --regression
"""
import argparse
import sys
import time
from datetime import datetime

from src.utils.logger import get_logger
from src.utils import report_generator as rpt

log = get_logger("qa_automation.main")
LAYERS = ["source", "master", "datamart", "aggregate", "reporting"]


def run_health_check():
    from src.connectors.db_connector import DBConnector
    from src.connectors.api_connector import APIConnector
    log.info("Running environment health check")
    ok = True
    try:
        db = DBConnector()
        for name, status in db.health_check().items():
            log.info("  DB %-20s : %s", name, status)
            ok &= status == "OK"
        db.dispose()
    except Exception as exc:
        log.error("  DB connection failed: %s", exc)
        ok = False
    try:
        r = APIConnector().health()
        log.info("  API /health          : HTTP %s", r.status_code)
        ok &= r.status_code == 200
    except Exception as exc:
        log.error("  API health failed: %s", exc)
        ok = False
    return ok


def run(business_date, layers, from_date=None, to_date=None, use_csv=False):
    from src.connectors.db_connector import DBConnector
    from src.connectors.file_connector import FileConnector
    from src.connectors.api_connector import APIConnector
    from src.connectors.report_connector import ReportConnector
    from src.validators.source_to_staging import SourceToStagingValidator
    from src.validators.staging_to_datamart import StagingToDataMartValidator
    from src.validators.aggregate_validator import AggregateValidator
    from src.validators.reporting_validator import ReportingValidator

    results, started = [], time.time()
    db = DBConnector()
    fc = FileConnector() if use_csv else None
    api, rc = APIConnector(), ReportConnector()

    log.info("=" * 70)
    log.info("QA validation run | business_date=%s | layers=%s", business_date, layers)
    log.info("=" * 70)

    try:
        if "source" in layers:
            v = SourceToStagingValidator(db, fc, api)
            for label, fn in (("Retail", lambda: v.run_retail(business_date, use_csv=use_csv)),
                              ("Distributor", lambda: v.run_distributor(business_date)),
                              ("Online", lambda: v.run_online(business_date, from_date, to_date))):
                log.info("--- Source-to-Staging : %s ---", label)
                try:
                    results.extend(fn())
                except Exception as exc:
                    log.exception("%s validation aborted: %s", label, exc)

        if "master" in layers or "source" in layers:
            log.info("--- Master / Reference Data Quality ---")
            try:
                results.extend(SourceToStagingValidator(db, fc, api).run_master())
            except Exception as exc:
                log.exception("Master validation aborted: %s", exc)

        if "datamart" in layers:
            log.info("--- Staging-to-Data Mart ---")
            v = StagingToDataMartValidator(db)
            for fn in (v.validate_product_harmonisation, v.validate_region_mapping,
                       v.validate_completeness):
                try:
                    results.extend(fn(business_date))
                except Exception as exc:
                    log.exception("Data mart validation error: %s", exc)
            for fn in (v.validate_channel_mapping, v.validate_customer_cleansing,
                       v.validate_customer_state, v.validate_net_amount,
                       v.validate_kyc_compliance):
                try:
                    results.append(fn(business_date))
                except Exception as exc:
                    log.exception("Data mart validation error: %s", exc)

        if "aggregate" in layers:
            log.info("--- Aggregate Models ---")
            v = AggregateValidator(db)
            try:
                results.extend(v.validate_region_summary(business_date))
                results.extend(v.validate_daily_summary(business_date))
                results.extend(v.validate_executive_summary(business_date))
                results.append(v.validate_channel_summary(business_date))
                results.append(v.validate_product_summary(business_date))
                results.append(v.validate_cross_consistency(business_date))
            except Exception as exc:
                log.exception("Aggregate validation error: %s", exc)

        if "reporting" in layers:
            log.info("--- Data Mart-to-Reporting ---")
            v = ReportingValidator(db, rc)
            try:
                results.extend(v.validate_views(business_date))
                results.extend(v.validate_dashboards(business_date))
                results.append(v.validate_filters(business_date))
                results.append(v.validate_freshness(business_date))
                results.append(v.validate_cross_report(business_date))
            except Exception as exc:
                log.exception("Reporting validation error: %s", exc)
    finally:
        db.dispose()

    log.info("Run complete in %ss with %s result(s)", round(time.time() - started, 2), len(results))
    if results:
        rpt.print_console(results)
        rpt.to_excel(results)
        rpt.to_html(results)

    s = rpt.summarise(results)
    return 0 if s["failed"] == 0 and s["errors"] == 0 else 1


def main():
    ap = argparse.ArgumentParser(description="QA validation suite for the sales data pipeline")
    ap.add_argument("--date", help="Business date to validate (YYYY-MM-DD)")
    ap.add_argument("--from-date", help="API range start (defaults to --date)")
    ap.add_argument("--to-date", help="API range end (defaults to --date)")
    ap.add_argument("--layer", action="append", choices=LAYERS,
                    help="Limit execution to specific layer(s). Repeatable.")
    ap.add_argument("--use-csv", action="store_true",
                    help="Read the retail source from the CSV file instead of retail_sales_raw")
    ap.add_argument("--regression", action="store_true", help="Run every layer")
    ap.add_argument("--health-check", action="store_true",
                    help="Verify DB and API connectivity, then exit")
    args = ap.parse_args()

    if args.health_check:
        return 0 if run_health_check() else 1
    if not args.date:
        ap.error("--date is required (e.g. --date 2026-05-01)")
    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        ap.error("--date must be in YYYY-MM-DD format")

    layers = LAYERS if (args.regression or not args.layer) else args.layer
    return run(args.date, layers, args.from_date, args.to_date, args.use_csv)


if __name__ == "__main__":
    sys.exit(main())
