"""Reusable, layer-agnostic validation primitives.

Every method returns a ValidationResult so the runner can aggregate,
report and log consistently.
"""
import time
import pandas as pd

from src.utils.result import ValidationResult
from src.utils.logger import get_logger

log = get_logger(__name__)
SAMPLE = 10


class BaseValidator:
    def __init__(self, tolerance=0.01):
        self.tolerance = tolerance

    @staticmethod
    def _res(tc, layer, desc, **kw):
        return ValidationResult(test_case_id=tc, layer=layer, description=desc, **kw)

    @staticmethod
    def _finish(res, ok, message, started):
        res.status = "PASS" if ok else "FAIL"
        res.message = message
        res.duration_sec = round(time.time() - started, 3)
        log.info("[%s] %s - %s", res.test_case_id, res.status, message)
        return res

    def validate_count(self, tc, layer, desc, source_count, target_count,
                       source_object="", target_object="", severity="Critical", risk_ref=""):
        started = time.time()
        res = self._res(tc, layer, desc, source_object=source_object,
                        target_object=target_object, expected=source_count,
                        actual=target_count, severity=severity, risk_ref=risk_ref)
        diff = (target_count or 0) - (source_count or 0)
        return self._finish(res, diff == 0,
                            f"source={source_count}, target={target_count}, difference={diff}", started)

    def validate_duplicates(self, tc, layer, desc, df, key,
                            target_object="", severity="High", risk_ref=""):
        started = time.time()
        res = self._res(tc, layer, desc, target_object=target_object,
                        expected="0 duplicates", severity=severity, risk_ref=risk_ref)
        if df is None or df.empty or key not in df.columns:
            res.status, res.message = "SKIPPED", f"No data or missing key column '{key}'"
            return res
        dup = df[df.duplicated(subset=[key], keep=False)]
        keys = sorted(dup[key].astype(str).unique().tolist()) if not dup.empty else []
        res.actual = f"{len(keys)} duplicate keys"
        res.failed_sample = keys[:SAMPLE]
        return self._finish(res, not keys, f"{len(keys)} duplicate value(s) for key '{key}'", started)

    def validate_not_null(self, tc, layer, desc, df, columns,
                          target_object="", severity="High", risk_ref=""):
        started = time.time()
        res = self._res(tc, layer, desc, target_object=target_object,
                        expected="0 nulls in mandatory fields", severity=severity, risk_ref=risk_ref)
        if df is None or df.empty:
            res.status, res.message = "SKIPPED", "No data returned"
            return res
        breaches = {}
        for col in columns:
            if col not in df.columns:
                breaches[col] = "COLUMN MISSING"
                continue
            n = int(df[col].isna().sum() + (df[col].astype(str).str.strip() == "").sum())
            if n:
                breaches[col] = n
        res.actual = breaches or "none"
        res.failed_sample = list(breaches.items())[:SAMPLE]
        return self._finish(res, not breaches, f"null/blank breaches: {breaches or 'none'}", started)

    def validate_key_sets(self, tc, layer, desc, source_keys, target_keys,
                          source_object="", target_object="", severity="High", risk_ref=""):
        started = time.time()
        res = self._res(tc, layer, desc, source_object=source_object,
                        target_object=target_object, severity=severity, risk_ref=risk_ref)
        s, t = set(map(str, source_keys)), set(map(str, target_keys))
        missing, extra = sorted(s - t), sorted(t - s)
        res.expected = f"{len(s)} source keys"
        res.actual = f"{len(t)} target keys | missing={len(missing)} extra={len(extra)}"
        res.failed_sample = {"missing_in_target": missing[:SAMPLE], "extra_in_target": extra[:SAMPLE]}
        return self._finish(res, not missing and not extra,
                            f"missing={len(missing)}, extra={len(extra)}", started)

    def validate_field_match(self, tc, layer, desc, src_df, tgt_df, key, columns,
                             source_object="", target_object="", severity="High", risk_ref=""):
        started = time.time()
        res = self._res(tc, layer, desc, source_object=source_object,
                        target_object=target_object, severity=severity, risk_ref=risk_ref)
        if src_df is None or tgt_df is None or src_df.empty or tgt_df.empty:
            res.status, res.message = "SKIPPED", "Source or target dataset empty"
            return res
        merged = src_df.merge(tgt_df, on=key, suffixes=("_src", "_tgt"), how="inner")
        mismatches = {}
        for col in columns:
            cs, ct = f"{col}_src", f"{col}_tgt"
            if cs not in merged.columns or ct not in merged.columns:
                continue
            left, right = merged[cs], merged[ct]
            ln, rn = pd.to_numeric(left, errors="coerce"), pd.to_numeric(right, errors="coerce")
            if ln.notna().all() and rn.notna().all():
                bad = merged[(ln - rn).abs() > self.tolerance]
            else:
                bad = merged[left.astype(str).str.strip() != right.astype(str).str.strip()]
            if not bad.empty:
                mismatches[col] = len(bad)
        res.expected = f"all {len(columns)} field(s) match on {len(merged)} joined rows"
        res.actual = mismatches or "all match"
        res.failed_sample = list(mismatches.items())[:SAMPLE]
        return self._finish(res, not mismatches, f"field mismatches: {mismatches or 'none'}", started)

    def validate_domain(self, tc, layer, desc, df, column, allowed,
                        target_object="", severity="Medium", risk_ref=""):
        started = time.time()
        res = self._res(tc, layer, desc, target_object=target_object,
                        expected=f"values in {allowed}", severity=severity, risk_ref=risk_ref)
        if df is None or df.empty or column not in df.columns:
            res.status, res.message = "SKIPPED", f"No data or missing column '{column}'"
            return res
        allowed_up = {str(a).upper() for a in allowed}
        bad = sorted({v for v in df[column].dropna().astype(str).str.upper().unique()
                      if v not in allowed_up})
        res.actual = bad or "all valid"
        res.failed_sample = bad[:SAMPLE]
        return self._finish(res, not bad, f"invalid values: {bad or 'none'}", started)

    def validate_pattern(self, tc, layer, desc, df, column, pattern,
                         target_object="", severity="Medium", risk_ref=""):
        started = time.time()
        res = self._res(tc, layer, desc, target_object=target_object,
                        expected=f"matches {pattern}", severity=severity, risk_ref=risk_ref)
        if df is None or df.empty or column not in df.columns:
            res.status, res.message = "SKIPPED", f"No data or missing column '{column}'"
            return res
        series = df[column].dropna().astype(str)
        bad = series[~series.str.match(pattern, na=False)]
        res.actual = f"{len(bad)} violation(s)"
        res.failed_sample = bad.head(SAMPLE).tolist()
        return self._finish(res, bad.empty, f"{len(bad)} value(s) failed pattern {pattern}", started)

    def validate_reference_integrity(self, tc, layer, desc, child_values, parent_values,
                                     source_object="", target_object="",
                                     severity="High", risk_ref=""):
        started = time.time()
        res = self._res(tc, layer, desc, source_object=source_object,
                        target_object=target_object, severity=severity, risk_ref=risk_ref)
        child = {str(v) for v in child_values if pd.notna(v)}
        parent = {str(v) for v in parent_values if pd.notna(v)}
        orphans = sorted(child - parent)
        res.expected = "0 orphan references"
        res.actual = f"{len(orphans)} orphan(s)"
        res.failed_sample = orphans[:SAMPLE]
        return self._finish(res, not orphans, f"{len(orphans)} orphan reference(s)", started)

    def validate_numeric(self, tc, layer, desc, expected, actual,
                         source_object="", target_object="",
                         severity="Critical", risk_ref="", tolerance=None):
        started = time.time()
        tol = self.tolerance if tolerance is None else tolerance
        res = self._res(tc, layer, desc, source_object=source_object,
                        target_object=target_object, expected=expected,
                        actual=actual, severity=severity, risk_ref=risk_ref)
        try:
            diff = abs(float(expected or 0) - float(actual or 0))
            ok = diff <= tol
            msg = f"expected={expected}, actual={actual}, diff={round(diff, 4)} (tol={tol})"
        except (TypeError, ValueError):
            ok, msg = False, f"non-numeric comparison: expected={expected}, actual={actual}"
        return self._finish(res, ok, msg, started)

    def validate_empty(self, tc, layer, desc, df, source_object="", target_object="",
                       severity="Critical", risk_ref="", expectation="0 rows"):
        started = time.time()
        res = self._res(tc, layer, desc, source_object=source_object,
                        target_object=target_object, expected=expectation,
                        severity=severity, risk_ref=risk_ref)
        n = 0 if df is None or df.empty else len(df)
        res.actual = f"{n} row(s)"
        if n and df is not None:
            res.failed_sample = df.head(SAMPLE).to_dict("records")
        return self._finish(res, n == 0, f"{n} offending row(s) found", started)
