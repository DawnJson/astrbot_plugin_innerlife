from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .emotion import Temperament

_CLOCK_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_clock(value: str) -> datetime.time:
    match = _CLOCK_RE.match(value.strip())
    if match is None:
        raise ValueError(f"time must look like HH:MM, got {value!r}")
    return datetime.time(int(match.group(1)), int(match.group(2)))


def _list(config: dict, key: str) -> list[str]:
    return [str(item).strip() for item in config.get(key, []) if str(item).strip()]


@dataclass(frozen=True)
class Settings:
    """Feature switches and numbers of steps 2-5; defaults suit an open-source install."""

    emotion: bool = True
    temperament: Temperament = field(default_factory=Temperament)
    insult_penalty: float = 12.0
    ignored_minutes: int = 15
    vent_affinity: float = 30.0

    memory: bool = True
    memory_half_life_days: float = 30.0
    memory_reinforce: float = 1.0
    memory_forget_threshold: float = 0.15
    memory_prompt_people: int = 4
    memory_prompt_per_person: int = 5
    forget_command: str = "/忘掉我"
    forget_done_message: str = "好 关于你的事我都忘了"
    person_keep_days: int = 0
    consolidation: bool = True
    consolidation_time: datetime.time = datetime.time(4, 0)
    background_provider_id: str = ""
    consolidation_max_chars: int = 24000

    diary: bool = True
    diary_recall_days: int = 3
    diary_prompt_chars: int = 1200
    diary_keep_daily_days: int = 30
    diary_keep_weekly_days: int = 180

    schedule: bool = True
    schedule_hour: int = 6
    weather: bool = True
    weather_refresh_minutes: int = 180
    news: bool = True
    news_interval_minutes: int = 120
    news_per_feed: int = 5
    news_keep_days: int = 7
    news_feeds: list[str] = field(default_factory=list)
    news_blocked_terms: list[str] = field(default_factory=list)
    holiday_url: str = "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json"
    city_name: str = "上海"
    latitude: float = 31.23
    longitude: float = 121.47
    timezone: str = "Asia/Shanghai"

    proactive: bool = True
    proactive_daily_cap: int = 6
    proactive_min_energy: float = 40.0
    proactive_quiet_minutes: int = 15
    proactive_min_gap_minutes: int = 45

    tools: bool = False
    allowed_tools: list[str] = field(default_factory=list)
    tool_daily_limits: dict[str, int] = field(default_factory=dict)
    tool_blocked_terms: list[str] = field(default_factory=list)
    draw: bool = False
    draw_tool_name: str = "image_generate"
    draw_tool_arguments: dict[str, Any] = field(default_factory=dict)
    draw_daily_limit: int = 5
    vision: bool = True
    search_rules: bool = True

    burst: bool = True
    burst_max_messages: int = 4
    burst_chars_per_second: float = 6.0

    @classmethod
    def from_config(cls, config: dict) -> Settings:
        defaults = cls()
        values: dict[str, Any] = {}
        for name, default in defaults.__dict__.items():
            if name in ("temperament", "consolidation_time"):
                continue
            raw = config.get(name, default)
            if isinstance(default, bool):
                values[name] = bool(raw)
            elif isinstance(default, int):
                values[name] = int(raw)
            elif isinstance(default, float):
                values[name] = float(raw)
            elif isinstance(default, list):
                values[name] = _list(config, name)
            elif isinstance(default, dict):
                # AstrBot's schema has no free-form dict type: these are JSON text.
                values[name] = json.loads(config.get(name) or "{}")
            else:
                values[name] = str(raw).strip()
        values["consolidation_time"] = parse_clock(
            str(config.get("consolidation_time", "04:00"))
        )
        base = Temperament()
        values["temperament"] = Temperament(
            **{
                name: float(config.get(name, getattr(base, name)))
                for name in base.__dict__
            }
        )
        values["tool_daily_limits"] = {
            str(name): int(limit) for name, limit in values["tool_daily_limits"].items()
        }
        return cls(**values)
