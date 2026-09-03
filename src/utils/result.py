"""Validation result model.

Submission 3 alignment (Assignment 3, Section 5):
  * Detailed results must show validation ID, expected value, actual value,
    VARIANCE, status and remarks  -> `variance` / `variance_pct` added.
  * The execution summary must count passed, failed, skipped and BLOCKED
    validations -> BLOCKED added to the status vocabulary.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional

# Status vocabulary
PASS = "PASS"
FAIL = "FAIL"
SKIPPED = "SKIPPED"
BLOCKED = "BLOCKED"      # could not execute (environment/access/dependency failure)
NOT_RUN = "NOT_RUN"

STATUSES = (PASS, FAIL, SKIPPED, BLOCKED, NOT_RUN)


def _as_number(value):
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
    variance: Any = None                    # actual - expected (numeric) or descriptive
    variance_pct: Optional[float] = None    # variance as % of expected
    environment: str = ""
    executed_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    duration_sec: float = 0.0

    # ---------------- variance ----------------
    def compute_variance(self):
        """Derive variance from expected/actual.

        Numeric pair  -> signed difference and percentage of expected.
        Non-numeric   -> a short descriptive variance, or 'No variance' when equal.
        """
        exp_n, act_n = _as_number(self.expected), _as_number(self.actual)

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
        d["remarks"] = d.pop("message")     # Section 5 column name
        return d

    # ---------------- convenience ----------------
    @property
    def passed(self):
        return self.status == PASS

    @property
    def is_defect(self):
        """Only genuine failures become defects; blocked/skipped do not."""
        return self.status == FAIL

    def mark_blocked(self, reason):
        """Validation could not be executed (env down, access denied, missing object)."""
        self.status = BLOCKED
        self.message = reason
        self.variance = "Not evaluated"
        return self

    def mark_skipped(self, reason):
        """Validation not applicable for this run (no data / optional column absent)."""
        self.status = SKIPPED
        self.message = reason
        self.variance = "Not evaluated"
        return self
