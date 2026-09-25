"""
Resolves the ONL-V03 BLOCKED status.

The lab API returned zero records for the requested date range, so
fetch_all_pages() never set a total and _pagination() reported BLOCKED.
An empty result set is not a blocker - ONL-V04 and ONL-V09 already treat
the same event as SKIPPED.

This script makes two edits:

  1. src/connectors/api_connector.py
     fetch_all_pages() sets the expected count to 0 (not None) when the
     response is empty, so "no data" is distinguishable from "no total
     reported".

  2. src/validators/source_to_staging.py
     _pagination() only BLOCKS when the count is genuinely undeterminable:
        empty response          -> SKIPPED
        records but no total    -> BLOCKED
        counts present          -> PASS / FAIL

Both files are backed up to *.bak before editing, and each result is
syntax-checked; on failure the original is restored.

Run from the project root:
    py fix_pagination.py
"""
import ast
import re
import shutil
import sys
from pathlib import Path

CONNECTOR = Path("src/connectors/api_connector.py")
VALIDATOR = Path("src/validators/source_to_staging.py")

NEW_PAGINATION = '''    def _pagination(self, df, total_reported, api_failed):
        res = self._res("ONL-V03", LAYER,
                        "All API records retrieved - collected rows equal the reported total",
                        source_object="/api/online-sales", expected=total_reported,
                        actual=None if df is None else len(df),
                        severity="Critical", risk_ref="R-SS-06")
        if api_failed:
            return self._blocked(res, "API extraction failed - pagination not verified")
        collected = 0 if df is None else len(df)
        if total_reported is None:
            # No expected count and no records: the range simply has no data,
            # which is skippable and consistent with ONL-V04 and ONL-V09.
            # Records present but no total: the envelope omitted the count, so
            # the completeness assertion genuinely cannot be made.
            if collected == 0:
                return self._skipped(
                    res, "No API records returned for the requested range")
            return self._blocked(
                res, "API response did not report a total record count, so "
                     "retrieval completeness cannot be verified")
        if total_reported == 0 and collected == 0:
            return self._skipped(res, "No API records returned for the requested range")
        res.status = "PASS" if collected == total_reported else "FAIL"
        res.message = f"collected={collected}, expected={total_reported}"
        res.compute_variance()
        log.info("[ONL-V03] %s - %s", res.status, res.message)
        return res
'''


def replace_method(path, method_name, replacement):
    """Replace a whole method definition, preserving the rest of the file."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    start = None
    for i, line in enumerate(lines):
        if re.match(rf"\s*def\s+{method_name}\s*\(", line):
            start = i
            break
    if start is None:
        return False, f"could not find '{method_name}' in {path}"

    indent = len(lines[start]) - len(lines[start].lstrip())
    end = len(lines)
    for j in range(start + 1, len(lines)):
        stripped = lines[j].lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        cur = len(lines[j]) - len(lines[j].lstrip())
        if cur <= indent and re.match(r"(def|class|@)", stripped):
            end = j
            break

    shutil.copy2(path, path.with_suffix(".py.bak"))
    new_text = "".join(lines[:start]) + replacement + "".join(lines[end:])
    try:
        ast.parse(new_text)
    except SyntaxError as exc:
        return False, f"patch would break {path}: {exc}"
    path.write_text(new_text, encoding="utf-8")
    return True, f"replaced {method_name} (lines {start + 1}-{end})"


def patch_connector():
    """Set the expected count to 0 on an empty response."""
    if not CONNECTOR.exists():
        return False, f"not found: {CONNECTOR}"
    text = CONNECTOR.read_text(encoding="utf-8")

    if 'total_reported = 0' in text and 'HTTP 404' in text:
        return True, "already patched"

    original = text
    shutil.copy2(CONNECTOR, CONNECTOR.with_suffix(".py.bak"))

    # 404 branch: record a known zero before breaking out
    text = text.replace(
        'log.info("No data for range %s..%s", from_date, to_date)\n                break',
        'log.info("No data for range %s..%s (HTTP 404)", from_date, to_date)\n'
        '                total_reported = 0\n                break')

    # plain-array branch: an empty first page means zero, not unknown
    text = text.replace(
        'if reported is None:\n'
        '                log.info("API returned an unpaginated array; '
        'treating as a single page")\n'
        '                total_reported = len(records)',
        'if reported is None:\n'
        '                if page == 1 and not chunk:\n'
        '                    log.info("API returned an empty array for %s..%s",\n'
        '                             from_date, to_date)\n'
        '                else:\n'
        '                    log.info("API returned an unpaginated array; "\n'
        '                             "treating as a single page")\n'
        '                total_reported = len(records)')

    if text == original:
        return False, ("could not locate the expected lines in fetch_all_pages; "
                       "the validator fix alone still resolves ONL-V03")
    try:
        ast.parse(text)
    except SyntaxError as exc:
        return False, f"patch would break {CONNECTOR}: {exc}"
    CONNECTOR.write_text(text, encoding="utf-8")
    return True, "fetch_all_pages now reports 0 for an empty response"


def main():
    if not VALIDATOR.exists():
        sys.exit(f"Not found: {VALIDATOR}\nRun this from the project root "
                 f"(the folder containing src/ and config/).")

    print("Applying ONL-V03 fix\n")

    ok, msg = patch_connector()
    print(f"  api_connector.py      : {'OK  ' if ok else 'SKIP'} {msg}")

    ok2, msg2 = replace_method(VALIDATOR, "_pagination", NEW_PAGINATION)
    print(f"  source_to_staging.py  : {'OK  ' if ok2 else 'FAIL'} {msg2}")

    if not ok2:
        sys.exit("\nThe validator patch failed. Original file restored from .bak.")

    print("\nBoth files parse cleanly. Backups written as *.py.bak")
    print("\nNext:")
    print("  py -m src.main --date 2026-05-01 --regression")
    print("  py -m src.utils.qa_summary --auto")
    print("\nExpect Blocked: 0 and a recommendation of NO-GO "
          "(not 'NO-GO - INCOMPLETE').")


if __name__ == "__main__":
    main()
