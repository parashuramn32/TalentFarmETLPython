"""Validation result object collected by the runner and rendered into reports."""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional


@dataclass
class ValidationResult:
    test_case_id: str
    layer: str
    description: str
    source_object: str = ""
    target_object: str = ""
    expected: Any = None
    actual: Any = None
    status: str = "NOT_RUN"          # PASS | FAIL | ERROR | SKIPPED
    severity: str = "Medium"
    risk_ref: str = ""
    message: str = ""
    failed_sample: Optional[list] = field(default=None)
    executed_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    duration_sec: float = 0.0

    def to_dict(self):
        d = asdict(self)
        if d.get("failed_sample"):
            d["failed_sample"] = str(d["failed_sample"])[:1000]
        return d

    @property
    def passed(self):
        return self.status == "PASS"
