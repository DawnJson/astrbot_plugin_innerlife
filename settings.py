from __future__ import annotations

import datetime
import json
import re
import zoneinfo
from dataclasses import dataclass
from typing import Any

from .emotion import Temperament

_CLOCK_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_clock(value: str) -> datetime.time:
    match = _CLOCK_RE.match(value.strip())
    if match is None:
        raise ValueError(f"time must look like HH:MM, got {value!r}")
    return datetime.time(int(match.group(1)), int(match.group(2)))


def _list(values: list) -> list[str]:
    return [str(item).strip() for item in values if str(item).strip()]


@dataclass(frozen=True)
class Settings:
    """Everything in _conf_schema.json; the defaults live only there."""

    # basic
    group_ids: frozenset[str]
    astrbot_persona_id: str
    persona_prompt: str
    work_background: str
    bot_names: list[str]
    allowed_link_domains: tuple[str, ...]
    secret_env_vars: list[str]
    error_reply_prefixes: tuple[str, ...]
    switch_command: str
    cooldown_seconds: float
    max_concurrency: int
    max_images: int
    background_provider_id: str
    daily_call_fuse: int
    vision: bool
    search_rules: bool
    timezone: str
    tz: zoneinfo.ZoneInfo

    # messages
    security_refusal_message: str
    image_limit_message: str
    empty_mention_message: str
    cooldown_message: str
    queue_busy_message: str
    model_error_message: str
    forget_done_message: str
    switch_disabled_note: str

    # emotion
    emotion: bool
    temperament: Temperament

    # memory and diary
    memory: bool
    memory_half_life_days: float
    forget_command: str
    person_keep_days: int
    consolidation_time: datetime.time
    diary: bool
    diary_keep_daily_days: int
    diary_keep_weekly_days: int

    # life
    schedule: bool
    weather: bool
    news: bool
    news_feeds: list[str]
    news_blocked_terms: list[str]
    holiday_url: str
    city_name: str
    latitude: float
    longitude: float

    # proactive topics
    proactive: bool
    proactive_quiet_minutes: int

    # talk
    talk_share: float
    talk_temperature: float
    talk_stamina_minutes: float
    talk_urge_hours: float
    talk_contagion: float
    talk_verbosity: float

    # drawing
    draw: bool
    draw_tool_name: str
    draw_tool_arguments: dict[str, Any]
    draw_daily_limit: int
    draw_blocked_terms: list[str]

    # burst
    burst: bool

    # stickers
    stickers: bool
    sticker_max_count: int
    sticker_max_bytes: int

    @classmethod
    def from_config(cls, config: dict) -> Settings:
        basic, messages, emo, mem, life, pro, talk, draw, burst, sticker = (
            config[group]
            for group in (
                "basic", "messages", "emotion", "memory", "life", "proactive", "talk", "draw",
                "burst", "sticker",
            )
        )
        group_ids = frozenset(_list(basic["group_ids"]))
        if not all(group_id.isdigit() for group_id in group_ids):
            raise ValueError("group_ids must contain QQ group numbers")
        switch_command = str(basic["switch_command"]).strip()
        if not switch_command:
            raise ValueError("switch_command must not be empty")
        persona_id = str(basic["astrbot_persona_id"]).strip()
        if not persona_id:
            raise ValueError("astrbot_persona_id must not be empty")
        timezone = str(basic["timezone"]).strip()
        for key in ("temperature", "stamina_minutes", "urge_hours", "verbosity"):
            if float(talk[key]) <= 0:
                raise ValueError(f"talk.{key} must be above 0")
        return cls(
            group_ids=group_ids,
            astrbot_persona_id=persona_id,
            persona_prompt=str(basic["persona_prompt"]).strip(),
            work_background=str(basic["work_background"]).strip(),
            bot_names=_list(basic["bot_names"]),
            allowed_link_domains=tuple(
                domain.casefold().strip(".") for domain in _list(basic["allowed_link_domains"])
            ),
            secret_env_vars=_list(basic["secret_env_vars"]),
            error_reply_prefixes=tuple(_list(basic["error_reply_prefixes"])),
            switch_command=switch_command,
            cooldown_seconds=float(basic["cooldown_seconds"]),
            max_concurrency=int(basic["max_concurrency"]),
            max_images=int(basic["max_images"]),
            background_provider_id=str(basic["background_provider_id"]).strip(),
            daily_call_fuse=int(basic["daily_call_fuse"]),
            vision=bool(basic["vision"]),
            search_rules=bool(basic["search_rules"]),
            timezone=timezone,
            tz=zoneinfo.ZoneInfo(timezone),
            security_refusal_message=messages["security_refusal_message"],
            image_limit_message=messages["image_limit_message"],
            empty_mention_message=messages["empty_mention_message"],
            cooldown_message=messages["cooldown_message"],
            queue_busy_message=messages["queue_busy_message"],
            model_error_message=messages["model_error_message"],
            forget_done_message=messages["forget_done_message"],
            switch_disabled_note=messages["switch_disabled_note"],
            emotion=bool(emo["enabled"]),
            temperament=Temperament(
                temper_flare=float(emo["temper_flare"]),
                temper_grudge=float(emo["temper_grudge"]),
                temper_soften=float(emo["temper_soften"]),
                mood_half_life_hours=float(emo["mood_half_life_hours"]),
                mood_baseline_valence=float(emo["mood_baseline_valence"]),
                affinity_fade_days=float(emo["affinity_fade_days"]),
            ),
            memory=bool(mem["enabled"]),
            memory_half_life_days=float(mem["memory_half_life_days"]),
            forget_command=str(mem["forget_command"]).strip(),
            person_keep_days=int(mem["person_keep_days"]),
            consolidation_time=parse_clock(str(mem["consolidation_time"])),
            diary=bool(mem["diary"]),
            diary_keep_daily_days=int(mem["diary_keep_daily_days"]),
            diary_keep_weekly_days=int(mem["diary_keep_weekly_days"]),
            schedule=bool(life["schedule"]),
            weather=bool(life["weather"]),
            news=bool(life["news"]),
            news_feeds=_list(life["news_feeds"]),
            news_blocked_terms=_list(life["news_blocked_terms"]),
            holiday_url=str(life["holiday_url"]).strip(),
            city_name=str(life["city_name"]).strip(),
            latitude=float(life["latitude"]),
            longitude=float(life["longitude"]),
            proactive=bool(pro["enabled"]),
            proactive_quiet_minutes=int(pro["proactive_quiet_minutes"]),
            talk_share=float(talk["share"]),
            talk_temperature=float(talk["temperature"]),
            talk_stamina_minutes=float(talk["stamina_minutes"]),
            talk_urge_hours=float(talk["urge_hours"]),
            talk_contagion=float(talk["contagion"]),
            talk_verbosity=float(talk["verbosity"]),
            draw=bool(draw["enabled"]),
            draw_tool_name=str(draw["draw_tool_name"]).strip(),
            # AstrBot's schema has no free-form dict type: this is JSON text.
            draw_tool_arguments=json.loads(draw["draw_tool_arguments"] or "{}"),
            draw_daily_limit=int(draw["draw_daily_limit"]),
            draw_blocked_terms=_list(draw["draw_blocked_terms"]),
            burst=bool(burst["enabled"]),
            stickers=bool(sticker["enabled"]),
            sticker_max_count=int(sticker["max_count"]),
            sticker_max_bytes=int(sticker["max_bytes"]),
        )
