"""CSV / flat-file connector for the Retail channel."""
from pathlib import Path
import pandas as pd

from src.utils.config_loader import load_config
from src.utils.logger import get_logger

log = get_logger(__name__)


class FileConnector:
    def __init__(self):
        self.cfg = load_config("source_config")["retail"]

    @property
    def path(self):
        return Path(self.cfg["file_path"])

    def read(self, dtype=str):
        p = self.path
        if not p.exists():
            raise FileNotFoundError(f"Retail CSV not found at: {p}")
        df = pd.read_csv(p, dtype=dtype)
        df.columns = [c.strip() for c in df.columns]
        log.info("Read %s rows x %s cols from %s", len(df), len(df.columns), p.name)
        return df

    def read_filtered(self):
        """Rows where transaction_status is in the configured filter."""
        df = self.read()
        col = self.cfg["status_column"]
        allowed = [s.upper() for s in self.cfg["status_filter"]]
        out = df[df[col].astype(str).str.upper().isin(allowed)].copy()
        log.info("Retail CSV filtered %s -> %s rows (%s in %s)", len(df), len(out), col, allowed)
        return out
