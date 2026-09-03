"""Loads YAML configuration and resolves ${ENV_VAR} placeholders."""
import os
import re
import yaml
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:      # python-dotenv is optional at import time
    pass

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")
_cache = {}


class ConfigError(RuntimeError):
    """Raised when configuration is missing or an env placeholder is unset."""


def _resolve(value, seen):
    if isinstance(value, str):
        def repl(m):
            name = m.group(1)
            env_val = os.getenv(name)
            if env_val is None:
                seen.append(name)
                return f"${{{name}}}"          # leave intact; reported below
            return env_val
        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _resolve(v, seen) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, seen) for v in value]
    return value


def load_config(name, use_cache=True):
    """Load config/<name>.yaml, resolving ${ENV_VAR} placeholders.

    Raises ConfigError listing every unset variable at once, so a first-run
    setup problem is fixed in one pass rather than one variable at a time.
    """
    if use_cache and name in _cache:
        return _cache[name]
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    missing = []
    resolved = _resolve(raw, missing)
    if missing:
        uniq = sorted(set(missing))
        raise ConfigError(
            f"Missing environment variable(s) required by {path.name}: {', '.join(uniq)}. "
            f"Copy .env.sample to .env and populate it.")
    if use_cache:
        _cache[name] = resolved
    return resolved


def load_rules():
    """validation_rules.yaml contains no env placeholders."""
    if "_rules" in _cache:
        return _cache["_rules"]
    with open(CONFIG_DIR / "validation_rules.yaml", "r", encoding="utf-8") as fh:
        _cache["_rules"] = yaml.safe_load(fh)
    return _cache["_rules"]


def environment_name():
    """Environment label recorded in the execution summary (Section 5)."""
    return os.getenv("QA_ENVIRONMENT", "UNSPECIFIED")


def clear_cache():
    _cache.clear()
