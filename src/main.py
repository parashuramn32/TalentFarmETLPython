"""
Python QA Automation - Financial Services Sales Data Pipeline
Entry point and orchestrator.

Validations run in dependency order so that upstream causes are triaged first:
    0 connectivity -> 1 source-to-staging -> 2 data mart -> 3 aggregates
    -> 4 reporting -> 5 regression

Usage:
    python -m src.main --health-check
    python -m src.main --date 2026-05-01
    python -m src.main --date 2026-05-01 --layer source --layer datamart
    python -m src.main --date 2026-05-01 --regression
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from src.utils.logger import get_logger, run_log_path
from src.utils import report_generator as rpt
from src.utils import defect_logger
from src.utils.result import FAIL, BLOCKED

log = get_logger("qa_automation.main")
LAYERS = ["source", "master", "datamart", "aggregate", "reporting"]
EVIDENCE_DIR = Path(__file__).resolve().parents[1] / "reports" / "evidence"


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
    log.info("Health check %s", "PASSED" if ok else "FAILED")
    return ok


def _write_evidence(results, business_date):
    """One JSON evidence file per failure or blocked check (Section 7)."""
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for r in results:
        if r.status not in (FAIL, BLOCKED):
            continue
        payload = {
            "validation_id": r.test_case_id, "layer": r.layer,
            "description": r.description, "risk_reference": r.risk_ref,
            "business_date": business_date, "environment": r.environment,
            "source_object": r.source_object, "target_object": r.target_object,
            "expected": r.expected, "actual": r.actual, "variance": r.variance,
            "status": r.status, "severity": r.severity, "remarks": r.message,
            "failed_sample": r.failed_sample, "executed_at": r.executed_at,
            "log_file": run_log_path().name,
        }
        path = EVIDENCE_DIR / f"{r.test_case_id}_{r.status}.json"
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        written.append(path.name)
    if written:
        log.info("Evidence files written to %s (%s file(s))", EVIDENCE_DIR, len(written))
    return written


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
    log.info("Log file: %s", run_log_path())
    log.info("=" * 70)

    def guard(label, fn):
        try:
            out = fn()
            results.extend(out if isinstance(out, list) else [out])
        except Exception as exc:
            log.exception("%s aborted: %s", label, exc)

    try:
        if "source" in layers:
            v = SourceToStagingValidator(db, fc, api)
            log.info("--- Layer 1: Source-to-Staging ---")
            guard("Retail", lambda: v.run_retail(business_date, use_csv=use_csv))
            guard("Distributor", lambda: v.run_distributor(business_date))
            guard("Online", lambda: v.run_online(business_date, from_date, to_date))

        if "master" in layers or "source" in layers:
            log.info("--- Layer 1: Master / Reference Data Quality ---")
            guard("Master", lambda: SourceToStagingValidator(db, fc, api)
                  .run_master(business_date))

        if "datamart" in layers:
            log.info("--- Layer 2: Staging-to-Data Mart ---")
            v = StagingToDataMartValidator(db)
            guard("Product harmonisation", lambda: v.validate_product_harmonisation(business_date))
            guard("Region mapping", lambda: v.validate_region_mapping(business_date))
            guard("Completeness", lambda: v.validate_completeness(business_date))
            guard("Channel mapping", lambda: v.validate_channel_mapping(business_date))
            guard("Customer cleansing", lambda: v.validate_customer_cleansing(business_date))
            guard("Customer state", lambda: v.validate_customer_state(business_date))
            guard("Net amount", lambda: v.validate_net_amount(business_date))
            guard("KYC compliance", lambda: v.validate_kyc_compliance(business_date))

        if "aggregate" in layers:
            log.info("--- Layer 3: Aggregate Models ---")
            v = AggregateValidator(db)
            guard("Region summary", lambda: v.validate_region_summary(business_date))
            guard("Daily summary", lambda: v.validate_daily_summary(business_date))
            guard("Executive summary", lambda: v.validate_executive_summary(business_date))
            guard("Channel summary", lambda: v.validate_channel_summary(business_date))
            guard("Product summary", lambda: v.validate_product_summary(business_date))
            guard("Cross consistency", lambda: v.validate_cross_consistency(business_date))

        if "reporting" in layers:
            log.info("--- Layer 4: Data Mart-to-Reporting ---")
            v = ReportingValidator(db, rc)
            guard("Views", lambda: v.validate_views(business_date))
            guard("Dashboards", lambda: v.validate_dashboards(business_date))
            guard("Filters", lambda: v.validate_filters(business_date))
            guard("Freshness", lambda: v.validate_freshness(business_date))
            guard("Cross report", lambda: v.validate_cross_report(business_date))
    finally:
        db.dispose()

    log.info("Run complete in %ss with %s result(s)",
             round(time.time() - started, 2), len(results))

    if not results:
        log.warning("No validation results produced. Check --layer arguments and connectivity.")
        return 1

    rpt.print_console(results, business_date)
    xlsx = rpt.to_excel(results, business_date=business_date)
    html = rpt.to_html(results, business_date=business_date)
    dpath, defects = defect_logger.to_excel(results, business_date=business_date)
    evidence = _write_evidence(results, business_date)

    print("Deliverables generated:")
    print(f"  Validation report (Excel) : {xlsx}")
    print(f"  Validation report (HTML)  : {html}")
    print(f"  Defect log                : {dpath}  ({len(defects)} defect(s))")
    print(f"  Evidence files            : {EVIDENCE_DIR}  ({len(evidence)} file(s))")
    print(f"  Execution log             : {run_log_path()}")
    print("\nNext step: python -m src.utils.qa_summary --auto\n")

    s = rpt.summarise(results, business_date)
    return 0 if s["failed"] == 0 and s["blocked"] == 0 else 1


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
        ap.error("--date is required (for example --date 2026-05-01)")
    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        ap.error("--date must be in YYYY-MM-DD format")

    layers = LAYERS if (args.regression or not args.layer) else args.layer
    try:
        return run(args.date, layers, args.from_date, args.to_date, args.use_csv)
    except Exception as exc:
        from src.utils.config_loader import ConfigError
        if isinstance(exc, ConfigError):
            log.error("Configuration error: %s", exc)
            return 2
        log.exception("Run failed: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
