"""MySQL connector - read-only query execution returning pandas DataFrames."""
import pandas as pd
from sqlalchemy import create_engine, text
from urllib.parse import quote_plus

from src.utils.config_loader import load_config
from src.utils.logger import get_logger

log = get_logger(__name__)


class DBConnector:
    """Manages read-only SQLAlchemy engines per logical database."""

    def __init__(self):
        cfg = load_config("db_config")
        self._my = cfg["mysql"]
        self._dbs = cfg["databases"]
        self._engines = {}

    def schema(self, logical_name):
        return self._dbs[logical_name]

    def _engine(self, logical_name):
        if logical_name not in self._dbs:
            raise KeyError(f"Unknown logical database '{logical_name}'. "
                           f"Valid: {list(self._dbs)}")
        if logical_name not in self._engines:
            uri = (f"mysql+pymysql://{self._my['user']}:{quote_plus(str(self._my['password']))}"
                   f"@{self._my['host']}:{self._my['port']}/{self._dbs[logical_name]}"
                   f"?charset={self._my.get('charset', 'utf8mb4')}")
            self._engines[logical_name] = create_engine(
                uri, pool_pre_ping=True,
                connect_args={"connect_timeout": self._my.get("connect_timeout", 30)})
            log.info("Engine created for %s -> %s", logical_name, self._dbs[logical_name])
        return self._engines[logical_name]

    def query(self, logical_name, sql, params=None):
        log.debug("[%s] %s", logical_name, " ".join(sql.split())[:200])
        with self._engine(logical_name).connect() as conn:
            return pd.read_sql(text(sql), conn, params=params or {})

    def scalar(self, logical_name, sql, params=None):
        df = self.query(logical_name, sql, params)
        return None if df.empty else df.iloc[0, 0]

    def count(self, logical_name, table, where=None, params=None):
        sql = f"SELECT COUNT(*) AS cnt FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return int(self.scalar(logical_name, sql, params) or 0)

    def health_check(self):
        status = {}
        for name in self._dbs:
            try:
                self.scalar(name, "SELECT 1")
                status[name] = "OK"
            except Exception as exc:
                status[name] = f"FAIL: {type(exc).__name__}"
                log.error("Health check failed for %s: %s", name, exc)
        return status

    def dispose(self):
        for eng in self._engines.values():
            eng.dispose()
        self._engines.clear()
