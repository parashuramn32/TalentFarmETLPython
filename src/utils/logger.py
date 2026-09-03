"""Central logging configuration - console + daily file handler."""
import logging
import sys
from pathlib import Path
from datetime import datetime

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LOG_DIR.mkdir(exist_ok=True)
_FMT = "%(asctime)s | %(levelname)-8s | %(name)-28s | %(message)s"


def get_logger(name="qa_automation"):
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(_FMT))
    logger.addHandler(console)
    fh = logging.FileHandler(LOG_DIR / f"execution_{datetime.now():%Y%m%d}.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter(_FMT))
    logger.addHandler(fh)
    return logger
