"""Shared test helpers: configs built from the schema defaults, and a stand-in for AstrBot's
logger where AstrBot is not installed (the tests that need AstrBot itself skip there)."""

from __future__ import annotations

import copy
import json
import logging
import sys
import types
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR.parent))

try:
    import astrbot.api  # noqa: F401

    HAS_ASTRBOT = True
except ModuleNotFoundError:
    HAS_ASTRBOT = False
    _api = types.ModuleType("astrbot.api")
    _api.logger = logging.getLogger("astrbot")
    _package = types.ModuleType("astrbot")
    _package.api = _api
    sys.modules["astrbot"] = _package
    sys.modules["astrbot.api"] = _api

from astrbot_plugin_innerlife.settings import Settings  # noqa: E402


def schema_defaults() -> dict:
    """The grouped config AstrBot would hand the plugin on a fresh install."""
    schema = json.loads((PLUGIN_DIR / "_conf_schema.json").read_text("utf-8"))
    return {
        group: {key: copy.deepcopy(item["default"]) for key, item in spec["items"].items()}
        for group, spec in schema.items()
    }


def make_config(**overrides) -> dict:
    """Schema defaults with overrides. `key=value` finds the key's group; `group__key=value`
    names it (needed for `enabled`)."""
    config = schema_defaults()
    for name, value in overrides.items():
        if "__" in name:
            group, key = name.split("__")
            groups = [group]
        else:
            key = name
            groups = [group for group, keys in config.items() if key in keys]
        assert len(groups) == 1 and key in config[groups[0]], f"unknown or ambiguous key {name}"
        config[groups[0]][key] = value
    return config


def make_settings(**overrides) -> Settings:
    return Settings.from_config(make_config(**overrides))
