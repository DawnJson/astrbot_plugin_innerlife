from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path


REPLY_SWITCH_STATE_VERSION = 1
_MAX_STATE_BYTES = 64 * 1024
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


@dataclass(frozen=True)
class ReplySwitchResult:
    enabled: bool
    changed: bool


class ReplySwitch:
    """Persist the per-group automatic reply state with an atomic file update."""

    def __init__(self, state_path: Path) -> None:
        self.state_path = Path(state_path)
        self._lock = asyncio.Lock()
        self._disabled_group_ids: set[str] = set()
        self.load_warning: str | None = None
        self._load_state()

    @staticmethod
    def _group_key(group_id: object) -> str:
        key = str(group_id or "").strip()
        if not key or len(key) > 32 or not key.isdigit():
            raise ValueError("group id must be a QQ group number")
        return key

    def _load_state(self) -> None:
        try:
            if self.state_path.stat().st_size > _MAX_STATE_BYTES:
                raise ValueError("reply switch state is too large")
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("reply switch state must be an object")
            if payload.get("version") != REPLY_SWITCH_STATE_VERSION:
                raise ValueError("unsupported reply switch state version")
            raw_group_ids = payload.get("disabled_group_ids")
            if not isinstance(raw_group_ids, list) or len(raw_group_ids) > 1000:
                raise ValueError("disabled group ids must be a bounded list")
            self._disabled_group_ids = {
                self._group_key(group_id) for group_id in raw_group_ids
            }
        except FileNotFoundError:
            return
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            self.load_warning = type(exc).__name__
            self._disabled_group_ids = set()

    def _write_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": REPLY_SWITCH_STATE_VERSION,
            "disabled_group_ids": sorted(self._disabled_group_ids),
        }
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=".reply-switch-",
            suffix=".tmp",
            dir=self.state_path.parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                descriptor = -1
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self.state_path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            Path(temporary_path).unlink(missing_ok=True)

    def is_enabled(self, group_id: object) -> bool:
        return self._group_key(group_id) not in self._disabled_group_ids

    async def set_enabled(
        self, group_id: object, enabled: bool
    ) -> ReplySwitchResult:
        group_key = self._group_key(group_id)
        requested = bool(enabled)
        async with self._lock:
            previous = group_key not in self._disabled_group_ids
            if previous == requested:
                return ReplySwitchResult(enabled=requested, changed=False)
            if requested:
                self._disabled_group_ids.discard(group_key)
            else:
                self._disabled_group_ids.add(group_key)
            try:
                self._write_state()
            except Exception:
                if previous:
                    self._disabled_group_ids.discard(group_key)
                else:
                    self._disabled_group_ids.add(group_key)
                raise
            return ReplySwitchResult(enabled=requested, changed=True)
