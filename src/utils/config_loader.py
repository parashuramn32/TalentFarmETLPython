"""Loads YAML configuration and resolves ${ENV_VAR} placeholders."""
import os
import re
import yaml
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _resolve(value):
    if isinstance(value, str):
        def repl(m):
            env_val = os.getenv(m.group(1))
            if env_val is None:
                raise EnvironmentError(
                    f"Required environment variable '{m.group(1)}' is not set. "
                    f"Copy .env.sample to .env and populate it.")
            return env_val
        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v) for v in value]
    return value


def load_config(name):
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        return _resolve(yaml.safe_load(fh))


def load_rules():
    """validation_rules.yaml contains no env placeholders."""
    with open(CONFIG_DIR / "validation_rules.yaml", "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def environment_name():
    """Environment label recorded in the execution summary (Section 5)."""
    return os.getenv("QA_ENVIRONMENT", "LAB")
