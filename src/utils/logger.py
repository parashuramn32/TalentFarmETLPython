"""Central logging configuration - console + per-run file handler.

The per-run log file is an Assignment 3 deliverable (Section 10, Execution Logs).
"""
import logging
import sys
from pathlib import Path
from datetime import datetime

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LOG_DIR.mkdir(exist_ok=True)
_FMT = "%(asctime)s | %(levelname)-8s | %(name)-28s | %(message)s"
RUN_STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
RUN_LOG = LOG_DIR / f"execution_{RUN_STAMP}.log"


def get_logger(name="qa_automation"):
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(_FMT))
    logger.addHandler(console)
    fh = logging.FileHandler(RUN_LOG, encoding="utf-8")
    fh.setFormatter(logging.Formatter(_FMT))
    logger.addHandler(fh)
    return logger


def run_log_path():
    return RUN_LOG
