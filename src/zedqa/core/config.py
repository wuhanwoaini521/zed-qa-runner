"""Project config (zedqa.toml) loading and saving.

Credentials are never stored here; only aliases and resource ids.
"""

from __future__ import annotations

from pathlib import Path

import tomli_w

try:  # py3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from zedqa.core.models import ZedqaConfig

CONFIG_NAME = "zedqa.toml"


def config_path(root: Path) -> Path:
    return root / CONFIG_NAME


def load_config(root: Path) -> ZedqaConfig:
    path = config_path(root)
    if not path.exists():
        return ZedqaConfig()
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return ZedqaConfig.model_validate(data)


def save_config(root: Path, config: ZedqaConfig) -> Path:
    path = config_path(root)
    data = config.model_dump(mode="json", exclude_none=True)
    with path.open("wb") as fh:
        tomli_w.dump(data, fh)
    return path


def default_toml() -> str:
    cfg = ZedqaConfig()
    cfg.resources.rules = []
    return tomli_w.dumps(cfg.model_dump(mode="json", exclude_none=True))
