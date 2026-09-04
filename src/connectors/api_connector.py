"""Online Sales REST API connector: pagination, retry and rate-limit handling.

Supports two response shapes:
  1. Envelope : {"total_records": N, "data": [...]}  - paginated
  2. Plain    : [ {...}, {...} ]                     - the whole result set

The lab API (api_app/app.py) returns shape 2, ignores page/page_size and
implements no authentication, so pagination is treated as a single page and
the X-API-Key header is only sent when a real key is configured.
"""
import time
import requests
import pandas as pd

from src.utils.config_loader import load_config
from src.utils.logger import get_logger

log = get_logger(__name__)

# Values that mean "this API has no authentication"
_NO_KEY = ("", "none", "not-required", "not_required", "n/a")


class APIConnector:
    def __init__(self):
        self.cfg = load_config("api_config")["online_sales_api"]
        self.base = self.cfg["base_url"].rstrip("/")
        self.session = requests.Session()
        headers = {"Content-Type": "application/json"}
        key = self.cfg.get("api_key")
        if key and str(key).strip().lower() not in _NO_KEY:
            headers["X-API-Key"] = key
        self.session.headers.update(headers)

    def _get(self, path, params=None, api_key_override=None):
        url = f"{self.base}{path}"
        headers = {"X-API-Key": api_key_override} if api_key_override is not None else None
        resp = None
        for attempt in range(1, self.cfg.get("max_retries", 3) + 1):
            resp = self.session.get(url, params=params, headers=headers,
                                    timeout=self.cfg.get("timeout_seconds", 60))
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

    @staticmethod
    def _unpack(payload):
        """Return (records, total_records_or_None) for either response shape."""
        if isinstance(payload, list):                       # plain array
            return payload, None
        if isinstance(payload, dict):
            for key in ("data", "records", "results", "items"):
                if isinstance(payload.get(key), list):
                    return payload[key], payload.get("total_records")
            inner = payload.get("data")
            if isinstance(inner, dict):                     # nested envelope
                for key in ("data", "records", "results", "items"):
                    if isinstance(inner.get(key), list):
                        return inner[key], inner.get("total_records")
        return [], None

    def fetch_all_pages(self, from_date, to_date, status=None):
        """Collect every record for the range.

        Returns (DataFrame, expected_count). When the API returns a plain array
        the expected count is the array length, so ONL-V03 still asserts that
        nothing was dropped between the response and the DataFrame.
        """
        records, page, total_reported = [], 1, None
        max_pages = self.cfg.get("max_pages", 200)

        while page <= max_pages:
            resp = self.get_page(from_date, to_date, page=page, status=status)
            if resp.status_code == 404:
                log.info("No data for range %s..%s", from_date, to_date)
                break
            resp.raise_for_status()
            payload = resp.json()
            chunk, reported = self._unpack(payload)
            if total_reported is None:
                total_reported = reported
            records.extend(chunk)
            log.info("Page %s -> %s record(s) (running total %s / %s)",
                     page, len(chunk), len(records),
                     total_reported if total_reported is not None else "unpaginated")

            if reported is None:
                log.info("API returned an unpaginated array; treating as a single page")
                total_reported = len(records)
                break
            if not chunk or len(records) >= total_reported:
                break
            page += 1
        else:
            log.warning("Pagination stopped at the max_pages guard (%s pages)", max_pages)

        return pd.DataFrame(records), total_reported
