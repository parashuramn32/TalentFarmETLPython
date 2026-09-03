"""Reusable, layer-agnostic validation primitives.

Submission 3 alignment:
  * every result carries a computed `variance` (Section 5)
  * infrastructure/access failures return BLOCKED, not FAIL (Section 5)
  * `message` is surfaced as the "Remarks" column in the execution report
"""
import time
import pandas as pd

from src.utils.result import ValidationResult, PASS, FAIL, BLOCKED
from src.utils.config_loader import environment_name
from src.utils.logger import get_logger

log = get_logger(__name__)
SAMPLE = 10


class BaseValidator:
    def __init__(self, tolerance=0.01):
        self.tolerance = tolerance
        self.environment = environment_name()

    # ---------------- lifecycle helpers ----------------
    def _res(self, tc, layer, desc, **kw):
        kw.setdefault("environment", self.environment)
        return ValidationResult(test_case_id=tc, layer=layer, description=desc, **kw)

    @staticmethod
    def _finish(res, ok, message, started):
        res.status = PASS if ok else FAIL
        res.message = message
        res.duration_sec = round(time.time() - started, 3)
        res.compute_variance()
        log.info("[%s] %s - %s", res.test_case_id, res.status, message)
        return res

    @staticmethod
    def _blocked(res, reason):
        res.mark_blocked(reason)
        log.warning("[%s] BLOCKED - %s", res.test_case_id, reason)
        return res

    @staticmethod
    def _skipped(res, reason):
        res.mark_skipped(reason)
        log.info("[%s] SKIPPED - %s", res.test_case_id, reason)
        return res

    # ---------------- count ----------------
    def validate_count(self, tc, layer, desc, source_count, target_count,
                       source_object="", target_object="", severity="Critical", risk_ref=""):
        started = time.time()
        res = self._res(tc, layer, desc, source_object=source_object,
                        target_object=target_object, expected=source_count,
                        actual=target_count, severity=severity, risk_ref=risk_ref)
        diff = (target_count or 0) - (source_count or 0)
        return self._finish(res, diff == 0,
                            f"source={source_count}, target={target_count}, difference={diff}", started)

    # ---------------- duplicates ----------------
    def validate_duplicates(self, tc, layer, desc, df, key,
                            target_object="", severity="High", risk_ref=""):
        started = time.time()
        res = self._res(tc, layer, desc, target_object=target_object,
                        expected=0, severity=severity, risk_ref=risk_ref)
        if df is None:
            return self._blocked(res, "Dataset unavailable - could not read target object")
        if df.empty:
            return self._skipped(res, "No rows for the business date")
        if key not in df.columns:
            return self._blocked(res, f"Key column '{key}' not present in the dataset")
        dup = df[df.duplicated(subset=[key], keep=False)]
        keys = sorted(dup[key].astype(str).unique().tolist()) if not dup.empty else []
        res.actual = len(keys)
        res.failed_sample = keys[:SAMPLE]
        return self._finish(res, not keys, f"{len(keys)} duplicate value(s) for key '{key}'", started)

    # ---------------- nulls ----------------
    def validate_not_null(self, tc, layer, desc, df, columns,
                          target_object="", severity="High", risk_ref=""):
        started = time.time()
        res = self._res(tc, layer, desc, target_object=target_object,
                        expected=0, severity=severity, risk_ref=risk_ref)
        if df is None:
            return self._blocked(res, "Dataset unavailable - could not read target object")
        if df.empty:
            return self._skipped(res, "No rows for the business date")
        breaches, missing_cols = {}, []
        for col in columns:
            if col not in df.columns:
                missing_cols.append(col)
                continue
            n = int(df[col].isna().sum() + (df[col].astype(str).str.strip() == "").sum())
            if n:
                breaches[col] = n
        if missing_cols:
            return self._blocked(res, f"Expected column(s) absent from target: {missing_cols}")
        res.actual = sum(breaches.values())
        res.failed_sample = list(breaches.items())[:SAMPLE]
        return self._finish(res, not breaches, f"null/blank breaches: {breaches or 'none'}", started)

    # ---------------- key set comparison ----------------
    def validate_key_sets(self, tc, layer, desc, source_keys, target_keys,
                          source_object="", target_object="", severity="High", risk_ref=""):
        started = time.time()
        res = self._res(tc, layer, desc, source_object=source_object,
                        target_object=target_object, severity=severity, risk_ref=risk_ref)
        s, t = set(map(str, source_keys)), set(map(str, target_keys))
        missing, extra = sorted(s - t), sorted(t - s)
        res.expected = len(s)
        res.actual = len(t)
        res.failed_sample = {"missing_in_target": missing[:SAMPLE], "extra_in_target": extra[:SAMPLE]}
        return self._finish(res, not missing and not extra,
                            f"source keys={len(s)}, target keys={len(t)}, "
                            f"missing={len(missing)}, extra={len(extra)}", started)

    # ---------------- field level comparison ----------------
    def validate_field_match(self, tc, layer, desc, src_df, tgt_df, key, columns,
                             source_object="", target_object="", severity="High", risk_ref=""):
        started = time.time()
        res = self._res(tc, layer, desc, source_object=source_object,
                        target_object=target_object, expected=0,
                        severity=severity, risk_ref=risk_ref)
        if src_df is None or tgt_df is None:
            return self._blocked(res, "Source or target dataset unavailable")
        if src_df.empty or tgt_df.empty:
            return self._skipped(res, "Source or target dataset empty for the business date")
        merged = src_df.merge(tgt_df, on=key, suffixes=("_src", "_tgt"), how="inner")
        if merged.empty:
            return self._skipped(res, f"No rows joined on '{key}'")
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
        res.actual = sum(mismatches.values())
        res.failed_sample = list(mismatches.items())[:SAMPLE]
        return self._finish(res, not mismatches,
                            f"{len(merged)} rows joined; field mismatches: "
                            f"{mismatches or 'none'}", started)

    # ---------------- domain ----------------
    def validate_domain(self, tc, layer, desc, df, column, allowed,
                        target_object="", severity="Medium", risk_ref=""):
        started = time.time()
        res = self._res(tc, layer, desc, target_object=target_object,
                        expected=0, severity=severity, risk_ref=risk_ref)
        if df is None:
            return self._blocked(res, "Dataset unavailable")
        if df.empty:
            return self._skipped(res, "No rows to evaluate")
        if column not in df.columns:
            return self._blocked(res, f"Column '{column}' not present in the dataset")
        allowed_up = {str(a).upper() for a in allowed}
        bad = sorted({v for v in df[column].dropna().astype(str).str.upper().unique()
                      if v not in allowed_up})
        res.actual = len(bad)
        res.failed_sample = bad[:SAMPLE]
        return self._finish(res, not bad,
                            f"values outside {allowed}: {bad or 'none'}", started)

    # ---------------- regex ----------------
    def validate_pattern(self, tc, layer, desc, df, column, pattern,
                         target_object="", severity="Medium", risk_ref=""):
        started = time.time()
        res = self._res(tc, layer, desc, target_object=target_object,
                        expected=0, severity=severity, risk_ref=risk_ref)
        if df is None:
            return self._blocked(res, "Dataset unavailable")
        if df.empty:
            return self._skipped(res, "No rows to evaluate")
        if column not in df.columns:
            return self._blocked(res, f"Column '{column}' not present in the dataset")
        series = df[column].dropna().astype(str)
        bad = series[~series.str.match(pattern, na=False)]
        res.actual = len(bad)
        res.failed_sample = bad.head(SAMPLE).tolist()
        return self._finish(res, bad.empty,
                            f"{len(bad)} value(s) failed pattern {pattern}", started)

    # ---------------- referential integrity ----------------
    def validate_reference_integrity(self, tc, layer, desc, child_values, parent_values,
                                     source_object="", target_object="",
                                     severity="High", risk_ref=""):
        started = time.time()
        res = self._res(tc, layer, desc, source_object=source_object,
                        target_object=target_object, expected=0,
                        severity=severity, risk_ref=risk_ref)
        child = {str(v) for v in child_values if pd.notna(v)}
        parent = {str(v) for v in parent_values if pd.notna(v)}
        if not parent:
            return self._blocked(res, "Parent/reference dataset is empty or unavailable")
        orphans = sorted(child - parent)
        res.actual = len(orphans)
        res.failed_sample = orphans[:SAMPLE]
        return self._finish(res, not orphans, f"{len(orphans)} orphan reference(s)", started)

    # ---------------- numeric ----------------
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
            msg = f"expected={expected}, actual={actual}, difference={round(diff, 4)} (tolerance={tol})"
        except (TypeError, ValueError):
            return self._blocked(res, f"Non-numeric comparison: expected={expected}, actual={actual}")
        return self._finish(res, ok, msg, started)

    # ---------------- empty-set assertion ----------------
    def validate_empty(self, tc, layer, desc, df, source_object="", target_object="",
                       severity="Critical", risk_ref=""):
        started = time.time()
        res = self._res(tc, layer, desc, source_object=source_object,
                        target_object=target_object, expected=0,
                        severity=severity, risk_ref=risk_ref)
        if df is None:
            return self._blocked(res, "Dataset unavailable - could not evaluate")
        n = len(df)
        res.actual = n
        if n:
            res.failed_sample = df.head(SAMPLE).to_dict("records")
        return self._finish(res, n == 0, f"{n} offending row(s) found", started)
