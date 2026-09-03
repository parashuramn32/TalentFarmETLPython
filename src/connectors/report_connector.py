"""Reporting (Flask) page connector - extracts displayed metric values."""
import re
import requests
import pandas as pd

from src.utils.config_loader import load_config
from src.utils.logger import get_logger

log = get_logger(__name__)
_NUM = re.compile(r"-?[\d,]+\.?\d*")


class ReportConnector:
    def __init__(self):
        cfg = load_config("reporting_config")
        self.app = cfg["reporting_app"]
        self.reports = cfg["reports"]
        self.base = self.app["base_url"].rstrip("/")

    def fetch(self, report_key, params=None):
        if report_key not in self.reports:
            raise KeyError(f"Unknown report '{report_key}'. Valid: {list(self.reports)}")
        url = f"{self.base}{self.reports[report_key]['path']}"
        resp = requests.get(url, params=params, timeout=self.app.get("timeout_seconds", 30))
        resp.raise_for_status()
        log.info("Fetched report %s (%s bytes)", report_key, len(resp.content))
        return resp.text

    def tables(self, report_key, params=None):
        from io import StringIO
        html = self.fetch(report_key, params)
        try:
            return pd.read_html(StringIO(html))     # StringIO avoids a pandas FutureWarning
        except ValueError:
            log.warning("No HTML tables found on report %s", report_key)
            return []

    def extract_metrics(self, report_key, labels, params=None):
        html = self.fetch(report_key, params)
        found = {}
        for label in labels:
            pattern = re.compile(re.escape(label) + r"\s*[:<][^\d\-]{0,120}?(-?[\d,]+\.?\d*)",
                                 re.IGNORECASE | re.DOTALL)
            m = pattern.search(html)
            found[label] = float(m.group(1).replace(",", "")) if m else None
        return found

    def page_total(self, report_key, measure_hints=("net", "sales", "total", "amount"),
                   params=None):
        """Sum the primary numeric measure column shown on a report page."""
        for t in self.tables(report_key, params):
            for col in t.columns:
                if any(k in str(col).lower() for k in measure_hints):
                    vals = pd.to_numeric(
                        t[col].astype(str).str.replace(r"[^\d.\-]", "", regex=True),
                        errors="coerce").dropna()
                    if not vals.empty:
                        return round(float(vals.sum()), 2)
        for v in self.extract_metrics(
                report_key, ["Total Net Sales", "Total Sales", "Net Sales"], params).values():
            if v is not None:
                return round(float(v), 2)
        return None
