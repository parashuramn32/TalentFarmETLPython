"""Online Sales REST API connector with pagination, retry and rate-limit handling."""
import time
import requests
import pandas as pd

from src.utils.config_loader import load_config
from src.utils.logger import get_logger

log = get_logger(__name__)


class APIConnector:
    def __init__(self):
        self.cfg = load_config("api_config")["online_sales_api"]
        self.base = self.cfg["base_url"].rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": self.cfg["api_key"],
                                     "Content-Type": "application/json"})

    def _get(self, path, params=None, api_key_override=None):
        url = f"{self.base}{path}"
        headers = {"X-API-Key": api_key_override} if api_key_override is not None else None
        resp = None
        for attempt in range(1, self.cfg.get("max_retries", 3) + 1):
            resp = self.session.get(url, params=params, headers=headers,
                                    timeout=self.cfg.get("timeout_seconds", 30))
            if resp.status_code == 429:
                wait = self.cfg.get("backoff_seconds", 5) * attempt
                log.warning("HTTP 429 rate limit. Backing off %ss (attempt %s)", wait, attempt)
                time.sleep(wait)
                continue
            return resp
        return resp

    def health(self):
        return self._get(self.cfg.get("health_endpoint", "/health"))

    def get_page(self, from_date, to_date, page=1, status=None):
        params = {"from_date": from_date, "to_date": to_date,
                  "page": page, "page_size": self.cfg.get("page_size", 500)}
        if status:
            params["status"] = status
        return self._get(self.cfg["endpoint"], params=params)

    def fetch_all_pages(self, from_date, to_date, status=None):
        """Walk every page; return (DataFrame, total_records_reported)."""
        records, page, total_reported = [], 1, None
        while True:
            resp = self.get_page(from_date, to_date, page=page, status=status)
            if resp.status_code == 404:
                log.info("No data for range %s..%s", from_date, to_date)
                break
            resp.raise_for_status()
            payload = resp.json()
            if total_reported is None:
                total_reported = payload.get("total_records")
            chunk = payload.get("data", [])
            records.extend(chunk)
            log.info("Page %s -> %s records (running total %s / %s)",
                     page, len(chunk), len(records), total_reported)
            if not chunk or (total_reported is not None and len(records) >= total_reported):
                break
            page += 1
        return pd.DataFrame(records), total_reported
