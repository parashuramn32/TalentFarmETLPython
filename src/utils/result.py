"""Validation result model.

Assignment 3 (Section 5) requires detailed results to carry validation ID,
expected, actual, VARIANCE, status and remarks, and the execution summary to
count passed / failed / skipped / BLOCKED.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional

PASS = "PASS"
FAIL = "FAIL"
SKIPPED = "SKIPPED"
BLOCKED = "BLOCKED"        # could not execute: environment, access or missing object
NOT_RUN = "NOT_RUN"

STATUSES = (PASS, FAIL, SKIPPED, BLOCKED, NOT_RUN)
NOT_EVALUATED = "Not evaluated"


def as_number(value):
    """Best-effort numeric coercion; returns None when not numeric."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


@dataclass
class ValidationResult:
    test_case_id: str
    layer: str
    description: str
    source_object: str = ""
    target_object: str = ""
    expected: Any = None
    actual: Any = None
    status: str = NOT_RUN
    severity: str = "Medium"
    risk_ref: str = ""
    message: str = ""                       # rendered as "Remarks" in the report
    failed_sample: Optional[Any] = field(default=None)
    variance: Any = None
    variance_pct: Optional[float] = None
    environment: str = ""
    executed_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    duration_sec: float = 0.0

    # ---------------- variance ----------------
    def compute_variance(self):
        """Signed difference for numeric pairs, descriptive text otherwise."""
        exp_n, act_n = as_number(self.expected), as_number(self.actual)

        if exp_n is not None and act_n is not None:
            diff = round(act_n - exp_n, 4)
            self.variance = diff
            self.variance_pct = round(100 * diff / exp_n, 4) if exp_n else None
            return self.variance

        if self.expected is None and self.actual is None:
            self.variance, self.variance_pct = None, None
        elif str(self.expected) == str(self.actual):
            self.variance, self.variance_pct = "No variance", None
        else:
            self.variance = f"expected '{self.expected}' vs actual '{self.actual}'"
            self.variance_pct = None
        return self.variance

    # ---------------- serialisation ----------------
    def to_dict(self):
        if self.variance is None and self.status in (FAIL, PASS):
            self.compute_variance()
        d = asdict(self)
        if d.get("failed_sample") is not None:
            d["failed_sample"] = str(d["failed_sample"])[:1000]
        d["remarks"] = d.pop("message")
        return d

    # ---------------- convenience ----------------
    @property
    def passed(self):
        return self.status == PASS

    @property
    def is_defect(self):
        """Only genuine failures become defects; blocked and skipped do not."""
        return self.status == FAIL

    def mark_blocked(self, reason):
        self.status = BLOCKED
        self.message = reason
        self.variance = NOT_EVALUATED
        return self

    def mark_skipped(self, reason):
        self.status = SKIPPED
        self.message = reason
        self.variance = NOT_EVALUATED
        return self
