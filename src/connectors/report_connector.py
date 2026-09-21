"""Reporting (Flask) page connector - extracts displayed metric values.

Observed page structure (executive dashboard, verified in the lab):

    <form>
        <input name="from_date" value="2026-05-01">
        <input name="to_date"   value="2026-06-30">
    </form>
    <section class="metrics">
        <div><label>Total Sales</label><span>109665929.00</span></div>
        <div><label>Insurance Premium</label><span>55734436.00</span></div>
        ...
    </section>

Two consequences:

1. There are no <table> elements. Values live in label/span KPI cards, so
   table parsing correctly yields nothing and label extraction is the primary
   strategy.

2. The page filters on from_date / to_date, not a single 'date' parameter.
   Passing 'date' was silently ignored, so the page returned the full default
   range (2026-05-01 to 2026-06-30) while the validator compared against a
   single business date. Date parameters are now sent in the form the page
   actually accepts.

When no trustworthy value can be extracted the method returns None so the
validator reports BLOCKED rather than comparing against a fabricated figure.
"""
import re
from io import StringIO

import requests
import pandas as pd

from src.utils.config_loader import load_config
from src.utils.logger import get_logger

log = get_logger(__name__)

# Labels used by the reporting pages for the headline net sales figure.
# Ordered longest-first so a specific label is preferred over a prefix of it
# (for example 'Total Net Sales' before 'Total Sales').
METRIC_LABELS = [
    "Total Net Sales Amount", "Total Net Sales", "Net Sales Amount",
    "Total Revenue", "Total Sales", "Net Sales",
]

# Column-name fragments identifying the measure column inside a table.
MEASURE_HINTS = ("net_sales", "net sales", "total_net", "total net",
                 "net_amount", "net amount", "sales_amount", "sales amount")


class ReportConnector:
    def __init__(self):
        cfg = load_config("reporting_config")
        self.app = cfg["reporting_app"]
        self.reports = cfg["reports"]
        self.base = self.app["base_url"].rstrip("/")

    # ---------------- parameters ----------------
    @staticmethod
    def _normalise_params(params):
        """Translate a single 'date' into the from_date/to_date the page expects.

        The reporting pages filter on a date range. A 'date' key alone is
        ignored by the application, which then renders its default range.
        """
        if not params:
            return None
        out = dict(params)
        single = out.pop("date", None)
        if single is not None:
            out.setdefault("from_date", single)
            out.setdefault("to_date", single)
        return out

    # ---------------- fetching ----------------
    def fetch(self, report_key, params=None):
        if report_key not in self.reports:
            raise KeyError(f"Unknown report '{report_key}'. Valid: {list(self.reports)}")
        url = f"{self.base}{self.reports[report_key]['path']}"
        query = self._normalise_params(params)
        resp = requests.get(url, params=query,
                            timeout=self.app.get("timeout_seconds", 30))
        resp.raise_for_status()
        log.info("Fetched report %s (%s bytes) params=%s",
                 report_key, len(resp.content), query)
        return resp.text

    def tables(self, report_key, params=None):
        """Parse HTML tables from a report page.

        pandas raises ValueError when no table is present and ImportError when
        no HTML parser is installed. Both are caught so a missing optional
        dependency degrades to label extraction rather than aborting the run.
        """
        html = self.fetch(report_key, params)
        try:
            return pd.read_html(StringIO(html))
        except ValueError:
            log.debug("No HTML tables on report %s; using label extraction", report_key)
            return []
        except ImportError as exc:
            log.warning("Cannot parse HTML tables on report %s (%s). "
                        "Install lxml or html5lib to enable table extraction.",
                        report_key, exc)
            return []

    # ---------------- extraction ----------------
    @staticmethod
    def _to_number(text):
        """Parse a displayed value such as '1,234.56' or '(1,234.56)'."""
        if text is None:
            return None
        s = str(text).strip()
        negative = s.startswith("(") and s.endswith(")")
        s = re.sub(r"[^\d.\-]", "", s)
        if not s or s in ("-", "."):
            return None
        try:
            val = float(s)
        except ValueError:
            return None
        return -val if negative else val

    @staticmethod
    def _flatten(html):
        """Strip tags to a single spaced line, preserving element boundaries."""
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html,
                      flags=re.S | re.I)          # drop JS/CSS blocks
        text = re.sub(r"<[^>]+>", " | ", text)    # tags become separators
        return re.sub(r"\s+", " ", text)

    def kpi_cards(self, report_key, params=None):
        """Return {label: value} for every KPI card on the page.

        Matches the observed <label>NAME</label><span>VALUE</span> structure.
        """
        html = self.fetch(report_key, params)
        cards = {}
        for m in re.finditer(
                r"<label[^>]*>\s*([^<]+?)\s*</label>\s*<span[^>]*>\s*([^<]+?)\s*</span>",
                html, re.I | re.S):
            val = self._to_number(m.group(2))
            if val is not None:
                cards[m.group(1).strip()] = val
        if cards:
            log.info("Report %s KPI cards: %s", report_key, cards)
        return cards

    def _from_tables(self, report_key, params):
        """Sum the measure column of the first table that has one."""
        for idx, t in enumerate(self.tables(report_key, params)):
            for col in t.columns:
                name = str(col).strip().lower()
                if any(h in name for h in MEASURE_HINTS):
                    vals = t[col].map(self._to_number).dropna()
                    if not vals.empty:
                        total = round(float(vals.sum()), 2)
                        log.info("Report %s: summed column '%s' from table %s -> %s",
                                 report_key, col, idx, total)
                        return total
        return None

    def _from_kpi_cards(self, report_key, params):
        """Match a known metric label against the KPI cards."""
        cards = self.kpi_cards(report_key, params)
        if not cards:
            return None
        lowered = {k.strip().lower(): v for k, v in cards.items()}
        for label in METRIC_LABELS:
            if label.lower() in lowered:
                log.info("Report %s: matched KPI card '%s' -> %s",
                         report_key, label, lowered[label.lower()])
                return lowered[label.lower()]
        return None

    def _from_labels(self, report_key, params):
        """Fallback: find a known metric label followed by a single number."""
        text = self._flatten(self.fetch(report_key, params))
        for label in METRIC_LABELS:
            m = re.search(re.escape(label) + r"[^0-9\-]{0,40}([\d,]+\.?\d*)",
                          text, re.IGNORECASE)
            if m:
                val = self._to_number(m.group(1))
                if val is not None:
                    log.info("Report %s: matched label '%s' -> %s",
                             report_key, label, val)
                    return val
        return None

    def extract_metrics(self, report_key, labels, params=None):
        """Return {label: value} for the supplied labels."""
        cards = self.kpi_cards(report_key, params)
        lowered = {k.strip().lower(): v for k, v in cards.items()}
        text = self._flatten(self.fetch(report_key, params))
        found = {}
        for label in labels:
            if label.strip().lower() in lowered:
                found[label] = lowered[label.strip().lower()]
                continue
            m = re.search(re.escape(label) + r"[^0-9\-]{0,40}([\d,]+\.?\d*)",
                          text, re.IGNORECASE)
            found[label] = self._to_number(m.group(1)) if m else None
        return found

    def page_total(self, report_key, measure_hints=None, params=None):
        """Extract the headline measure from a report page.

        Strategy order: HTML table -> KPI card -> labelled text.
        Returns None when no trustworthy value can be found, so the caller
        reports BLOCKED rather than comparing against a fabricated figure.
        """
        for extractor in (self._from_tables, self._from_kpi_cards, self._from_labels):
            total = extractor(report_key, params)
            if total is not None:
                return total
        log.warning("Report %s: no table measure column, KPI card or recognised "
                    "metric label; cannot extract a trustworthy total", report_key)
        return None
