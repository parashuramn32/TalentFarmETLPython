"""Generates Excel and HTML validation reports from ValidationResult objects."""
from pathlib import Path
from datetime import datetime
import pandas as pd

from src.utils.logger import get_logger

log = get_logger(__name__)
REPORT_DIR = Path(__file__).resolve().parents[2] / "reports"
REPORT_DIR.mkdir(exist_ok=True)

STATUS_COLOR = {"PASS": "C6EFCE", "FAIL": "FFC7CE", "ERROR": "FFEB9C", "SKIPPED": "D9D9D9"}
STATUS_FONT = {"PASS": "006100", "FAIL": "9C0006", "ERROR": "9C6500", "SKIPPED": "595959"}


def _frame(results):
    return pd.DataFrame([r.to_dict() for r in results])


def summarise(results):
    df = _frame(results)
    if df.empty:
        return {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "pass_rate": 0.0}
    total = len(df)
    passed = int((df["status"] == "PASS").sum())
    failed = int((df["status"] == "FAIL").sum())
    errors = int((df["status"] == "ERROR").sum())
    skipped = int((df["status"] == "SKIPPED").sum())
    executed = total - skipped
    return {"total": total, "passed": passed, "failed": failed, "errors": errors,
            "skipped": skipped, "pass_rate": round(100 * passed / executed, 1) if executed else 0.0}


def to_excel(results, filename=None):
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    df = _frame(results)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORT_DIR / (filename or f"validation_report_{stamp}.xlsx")
    summary = summarise(results)

    cols = ["test_case_id", "layer", "description", "risk_ref", "source_object",
            "target_object", "expected", "actual", "status", "severity",
            "message", "duration_sec", "executed_at"]
    df = df[[c for c in cols if c in df.columns]]

    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        pd.DataFrame([summary]).to_excel(xl, sheet_name="Summary", index=False)
        df.to_excel(xl, sheet_name="Results", index=False)
        fails = df[df["status"].isin(["FAIL", "ERROR"])]
        if not fails.empty:
            fails.to_excel(xl, sheet_name="Defects", index=False)
        if "layer" in df.columns:
            df.groupby(["layer", "status"]).size().unstack(fill_value=0).reset_index() \
              .to_excel(xl, sheet_name="By Layer", index=False)

        thin = Side(style="thin", color="A6A6A6")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)
        for sheet in xl.book.worksheets:
            for cell in sheet[1]:
                cell.font = Font(bold=True, color="FFFFFF", size=10, name="Arial")
                cell.fill = PatternFill("solid", start_color="1F3864")
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                cell.border = border
            headers = [c.value for c in sheet[1]]
            if "status" in headers:
                idx = headers.index("status") + 1
                for row in range(2, sheet.max_row + 1):
                    c = sheet.cell(row, idx)
                    if str(c.value) in STATUS_COLOR:
                        c.fill = PatternFill("solid", start_color=STATUS_COLOR[str(c.value)])
                        c.font = Font(bold=True, color=STATUS_FONT[str(c.value)],
                                      size=9, name="Arial")
                        c.alignment = Alignment(horizontal="center")
            for i, col in enumerate(headers, 1):
                width = 46 if col in ("description", "message") else \
                        26 if col in ("source_object", "target_object", "expected", "actual") else 15
                sheet.column_dimensions[get_column_letter(i)].width = width
            sheet.freeze_panes = "A2"

    log.info("Excel report written: %s", path)
    return path


def to_html(results, filename=None):
    df = _frame(results)
    s = summarise(results)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORT_DIR / (filename or f"validation_report_{stamp}.html")

    rows = []
    for _, r in df.iterrows():
        cls = str(r.get("status", "")).lower()
        rows.append(
            f"<tr class='{cls}'><td>{r.get('test_case_id','')}</td>"
            f"<td>{r.get('layer','')}</td><td>{r.get('description','')}</td>"
            f"<td>{r.get('risk_ref','')}</td><td>{r.get('expected','')}</td>"
            f"<td>{r.get('actual','')}</td><td class='st'>{r.get('status','')}</td>"
            f"<td>{r.get('severity','')}</td><td>{r.get('message','')}</td></tr>")

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>QA Validation Report</title><style>
body{{font-family:Arial,Helvetica,sans-serif;margin:24px;color:#222}}
h1{{color:#1F3864;margin-bottom:4px}} .sub{{color:#666;margin-bottom:18px}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}}
.card{{border:1px solid #ddd;border-radius:8px;padding:12px 18px;min-width:110px}}
.card .n{{font-size:26px;font-weight:bold}} .card .l{{font-size:12px;color:#666}}
.pass .n{{color:#137333}} .fail .n{{color:#c00}} .err .n{{color:#b8860b}} .skip .n{{color:#666}}
table{{border-collapse:collapse;width:100%;font-size:12px}}
th{{background:#1F3864;color:#fff;padding:8px;text-align:left;position:sticky;top:0}}
td{{border:1px solid #ddd;padding:6px;vertical-align:top}}
tr.pass td.st{{background:#C6EFCE;color:#006100;font-weight:bold;text-align:center}}
tr.fail td.st{{background:#FFC7CE;color:#9C0006;font-weight:bold;text-align:center}}
tr.error td.st{{background:#FFEB9C;color:#9C6500;font-weight:bold;text-align:center}}
tr.skipped td.st{{background:#D9D9D9;color:#595959;font-weight:bold;text-align:center}}
</style></head><body>
<h1>Python QA Automation &ndash; Validation Report</h1>
<div class="sub">Financial Services Sales Data Pipeline &middot; generated {datetime.now():%d-%b-%Y %H:%M:%S}</div>
<div class="cards">
<div class="card"><div class="n">{s['total']}</div><div class="l">Total</div></div>
<div class="card pass"><div class="n">{s['passed']}</div><div class="l">Passed</div></div>
<div class="card fail"><div class="n">{s['failed']}</div><div class="l">Failed</div></div>
<div class="card err"><div class="n">{s['errors']}</div><div class="l">Errors</div></div>
<div class="card skip"><div class="n">{s['skipped']}</div><div class="l">Skipped</div></div>
<div class="card"><div class="n">{s['pass_rate']}%</div><div class="l">Pass Rate</div></div>
</div>
<table><thead><tr><th>Test Case</th><th>Layer</th><th>Description</th><th>Risk Ref</th>
<th>Expected</th><th>Actual</th><th>Status</th><th>Severity</th><th>Message</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></body></html>"""

    path.write_text(html, encoding="utf-8")
    log.info("HTML report written: %s", path)
    return path


def print_console(results):
    s = summarise(results)
    print("\n" + "=" * 78)
    print("  VALIDATION EXECUTION SUMMARY")
    print("=" * 78)
    print(f"  Total: {s['total']}   Passed: {s['passed']}   Failed: {s['failed']}   "
          f"Errors: {s['errors']}   Skipped: {s['skipped']}   Pass rate: {s['pass_rate']}%")
    print("-" * 78)
    for r in results:
        icon = {"PASS": "[PASS]", "FAIL": "[FAIL]",
                "ERROR": "[ERR ]", "SKIPPED": "[SKIP]"}.get(r.status, "[    ]")
        print(f"  {icon} {r.test_case_id:<9} {r.layer:<22} {r.description[:44]}")
        if r.status in ("FAIL", "ERROR"):
            print(f"          -> {r.message}")
    print("=" * 78 + "\n")
