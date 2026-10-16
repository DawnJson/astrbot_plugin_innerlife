from __future__ import annotations

import re

SPLIT_MARKER = "<sp>"
MAX_PART_CHARS = 80
MAX_TOTAL_CHARS = 240
MIN_DELAY = 0.8
MAX_DELAY = 5.0
CHARS_PER_SECOND = 6.0  # typing speed she imitates between burst messages

_SPLIT_RE = re.compile(r"<\s*sp\s*/?\s*>", re.IGNORECASE)
_STRUCTURE_RE = re.compile(r"(?m)^\s*(?:\d+\s*[.、)）]|[-•*·]\s)|```")


def split_parts(text: str) -> list[str]:
    return [part.strip() for part in _SPLIT_RE.split(text) if part.strip()]


def burst_cap(energy: float, arousal: float) -> int:
    """How many messages she may split a casual reply into: tired means one, lively means more."""
    if energy < 30:
        cap = 1
    elif energy < 65:
        cap = 2
    else:
        cap = 3
    return cap + 1 if arousal >= 0.7 else cap


def plan_burst(text: str, cap: int) -> list[str]:
    """The messages to send for one model answer; anything long or structured stays one message."""
    parts = split_parts(text)
    if len(parts) <= 1:
        return parts or [text.strip()]
    if (
        cap < 2
        or _STRUCTURE_RE.search(text)
        or any(len(part) > MAX_PART_CHARS for part in parts)
        or sum(map(len, parts)) > MAX_TOTAL_CHARS
    ):
        return [" ".join(parts)]
    return [*parts[: cap - 1], " ".join(parts[cap - 1 :])]


def typing_delay(text: str, jitter: float) -> float:
    """Pause before the next message, about how long it takes to type; jitter is in 0..1."""
    return max(MIN_DELAY, min(MAX_DELAY, len(text) / CHARS_PER_SECOND + jitter * 0.8))


def burst_rules(cap: int) -> str:
    if cap < 2:
        return ""
    return (
        f"【分条】闲聊时如果你想多说几句，可以拆成 2 到 {cap} 条短消息连着发，条与条之间写 {SPLIT_MARKER}，"
        "每条像随手打的一小句；程序会隔几秒依次发出。只在闲聊、接话、吐槽时这样；回答问题、"
        "带步骤或列表的内容一律只发一条，不要写分条标记。多数时候一条就够，不要每次都拆。"
    )
