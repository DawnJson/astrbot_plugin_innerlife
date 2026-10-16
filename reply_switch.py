from __future__ import annotations

import unicodedata

_ACTIONS = {"开启": "enable", "关闭": "disable"}


def _normalize_command(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.translate(
        {ord(character): None for character in "\u200b\u200c\u200d\ufeff"}
    )
    return " ".join(text.casefold().split())[:64]


def canonical_reply_switch_command(text: object, command: str = "/机器人") -> str | None:
    """Return the exact reply-switch action requested by a group command."""
    prefix = f"{_normalize_command(command)} "
    normalized = _normalize_command(text)
    if not normalized.startswith(prefix):
        return None
    return _ACTIONS.get(normalized.removeprefix(prefix))


def is_group_manager(raw_event: object) -> bool:
    if not isinstance(raw_event, dict):
        return False
    sender = raw_event.get("sender")
    if not isinstance(sender, dict):
        return False
    return str(sender.get("role", "")).casefold() in {"owner", "admin"}
