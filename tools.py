from __future__ import annotations

import base64
import datetime
import re
import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from mcp.types import CallToolResult, ImageContent, TextContent

from astrbot.api import star
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from .store import Store

# Always on: minors, sexual content, gore, illegal how-tos, extremist propaganda.
# Deployments add their own (the schema default has political figures) on top.
BUILTIN_BLOCKED_TERMS = [
    # 未成年人性相关
    "lolicon", "shota", "萝莉", "正太", "幼女", "幼童", "未成年性", "儿童色情", "childporn", "cp资源",
    # 露骨性内容
    "porn", "hentai", "nsfw", "r18", "色情", "裸体", "裸照", "露点", "做爱", "性交", "口交", "自慰", "阴茎", "阴道", "乳头",
    # 血腥暴力
    "gore", "血腥", "断肢", "肢解", "斩首", "尸体特写", "虐杀", "开膛",
    # 违法操作指引
    "制造炸弹", "炸药配方", "制毒", "冰毒制作", "合成毒品", "枪支制作", "洗钱教程", "伪造证件", "buildabomb", "makemeth",
    # 极端主义宣传
    "islamicstate", "圣战宣传", "恐怖组织", "纳粹", "nazi", "白人至上", "种族清洗",
]

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_IMAGE_MIME_SUFFIX = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
# An absolute path ending in an image suffix, wherever it sits in the tool's text.
_IMAGE_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|/)[^\s\"'<>|]*?\.(?:png|jpe?g|webp|gif)(?!\w|\.\w)", re.IGNORECASE
)


def _normalize(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, Sequence):
        for item in value:
            yield from _strings(item)


class ToolGate:
    """Runs the one tool she draws with: blocked words and a daily count are checked first,
    and the images it makes are collected from its result."""

    def __init__(
        self,
        blocked_terms: Sequence[str],
        daily_limit: int,
        store: Store,
        image_dir: Path,
        tz: datetime.tzinfo,
    ) -> None:
        """image_dir: the only directory the images may live in (the tool should save there)."""
        self._blocked = [t for t in (_normalize(term) for term in blocked_terms) if t]
        self._daily_limit = daily_limit
        self._store = store
        self._image_dir = image_dir.resolve()
        self._tz = tz

    def is_blocked(self, text: str) -> bool:
        normalized = _normalize(text)
        return any(term in normalized for term in self._blocked)

    def _today(self) -> str:
        return datetime.datetime.now(self._tz).date().isoformat()

    def has_quota(self, name: str) -> bool:
        return self._store.tool_uses(self._today(), name) < self._daily_limit

    def images_of(self, result: Any) -> list[str]:
        """Images in a tool result: MCP ImageContent decoded into the image dir or, when the
        result has none, the image paths in its text that lie inside the image dir."""
        if isinstance(result, CallToolResult):
            contents = result.content
            text = "\n".join(c.text for c in contents if isinstance(c, TextContent))
        else:
            contents, text = [], str(result)
        paths: list[str] = []
        for content in contents:
            suffix = _IMAGE_MIME_SUFFIX.get(getattr(content, "mimeType", ""))
            if isinstance(content, ImageContent) and suffix:
                self._image_dir.mkdir(parents=True, exist_ok=True)
                path = self._image_dir / f"{uuid.uuid4().hex}{suffix}"
                path.write_bytes(base64.b64decode(content.data))
                paths.append(str(path))
        if paths:
            return paths
        for raw in _IMAGE_PATH_RE.findall(text):
            path = Path(raw).resolve()
            if (
                path.suffix.lower() in _IMAGE_SUFFIXES
                and path.is_file()
                and path.is_relative_to(self._image_dir)
                and str(path) not in paths
            ):
                paths.append(str(path))
        return paths

    async def invoke(
        self,
        context: star.Context,
        event: AstrMessageEvent | None,
        tool_name: str,
        arguments: dict,
    ) -> list[str]:
        """Run a registered tool outside the model's tool loop; the paths of the images it made,
        empty when the tool is unavailable, the arguments are blocked, the day's count is used
        up, or the tool failed. `event` is None when there is none (a proactive topic)."""
        tool = context.get_llm_tool_manager().get_full_tool_set().get_tool(tool_name)
        if (
            tool is None
            or not tool.active
            or any(self.is_blocked(text) for text in _strings(arguments))
            or not self.has_quota(tool_name)
        ):
            return []
        run_context = ContextWrapper(context=SimpleNamespace(context=context, event=event))
        result = await tool.call(run_context, **arguments)
        if isinstance(result, CallToolResult) and result.isError:
            return []
        self._store.add_tool_use(self._today(), tool_name)
        return self.images_of(result)
