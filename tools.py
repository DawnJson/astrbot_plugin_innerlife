from __future__ import annotations

import json
import os
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from mcp.types import CallToolResult, TextContent

from astrbot.api import star
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.platform.astr_message_event import AstrMessageEvent


DEFAULT_TOOL_BLOCKED_TERMS = [
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
    # 政治人物
    "习近平", "毛泽东", "邓小平", "江泽民", "胡锦涛", "李克强", "彭丽媛", "蔡英文", "赖清德", "特朗普", "拜登", "普京",
    "泽连斯基", "金正恩", "安倍晋三", "xijinping", "donaldtrump", "joebiden", "vladimirputin", "zelensky", "kimjongun",
]

_SAVED_RE = re.compile(r"saved: (.+?) \(")
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_IMAGES_EXTRA_KEY = "innerlife_tool_images"
_REFUSE_BLOCKED = "工具拒绝了这次请求：内容不符合使用规则。不要换说法重试，直接告诉对方这个不画/不查。"
_REFUSE_QUOTA = "这个工具今天的使用次数已经用完，明天再试。直接告诉对方今天不弄了。"


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


def _result_text(result: Any) -> str:
    if isinstance(result, CallToolResult):
        return "\n".join(c.text for c in result.content if isinstance(c, TextContent))
    return str(result)


class _GatedTool(FunctionTool):
    """A copy of a registered tool that checks the gate before delegating."""

    def __init__(self, tool: FunctionTool, gate: ToolGate) -> None:
        super().__init__(
            name=tool.name, description=tool.description, parameters=tool.parameters
        )
        self._wrapped = tool
        self._gate = gate

    async def call(self, context: Any, **kwargs: Any) -> Any:
        gate = self._gate
        if any(gate.is_blocked(text) for text in _strings(kwargs)):
            return _REFUSE_BLOCKED
        if not gate.has_quota(self.name):
            return _REFUSE_QUOTA
        result = await self._wrapped.call(context, **kwargs)
        if isinstance(result, CallToolResult) and result.isError:
            return result
        gate.count(self.name)
        gate.record_images(context.context.event, _result_text(result))
        return result


class ToolGate:
    def __init__(
        self,
        blocked_terms: Sequence[str],
        daily_limits: Mapping[str, int],
        usage_path: Path,
        image_dir: Path | None = None,
    ) -> None:
        """image_dir: the only directory tool-produced images may live in (default
        `<usage_path dir>/images`; the deployer points the image tool's save dir there)."""
        self._blocked = [t for t in (_normalize(term) for term in blocked_terms) if t]
        self._limits = dict(daily_limits)
        self._usage_path = usage_path
        self._image_dir = (image_dir or usage_path.parent / "images").resolve()
        self._day = ""
        self._counts: dict[str, int] = {}
        if usage_path.exists():
            data = json.loads(usage_path.read_text("utf-8"))
            self._day, self._counts = data["date"], data["counts"]

    def is_blocked(self, text: str) -> bool:
        normalized = _normalize(text)
        return any(term in normalized for term in self._blocked)

    def _roll_day(self) -> None:
        today = date.today().isoformat()
        if self._day != today:
            self._day, self._counts = today, {}

    def has_quota(self, name: str) -> bool:
        self._roll_day()
        return name not in self._limits or self._counts.get(name, 0) < self._limits[name]

    def count(self, name: str) -> None:
        self._roll_day()
        self._counts[name] = self._counts.get(name, 0) + 1
        tmp = self._usage_path.with_name(self._usage_path.name + ".tmp")
        self._usage_path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            json.dumps({"date": self._day, "counts": self._counts}), "utf-8"
        )
        os.replace(tmp, self._usage_path)

    def record_images(self, event: AstrMessageEvent, text: str) -> None:
        for raw in _SAVED_RE.findall(text):
            path = Path(raw).resolve()
            if (
                path.suffix.lower() in _IMAGE_SUFFIXES
                and path.is_file()
                and path.is_relative_to(self._image_dir)
            ):
                images = event.get_extra(_IMAGES_EXTRA_KEY) or []
                event.set_extra(_IMAGES_EXTRA_KEY, [*images, str(path)])

    def toolset_for(
        self, context: star.Context, allowed_tool_names: Sequence[str]
    ) -> ToolSet | None:
        registered = context.get_llm_tool_manager().get_full_tool_set()
        toolset = ToolSet()
        for name in allowed_tool_names:
            tool = registered.get_tool(name)
            if tool is not None and tool.active:
                toolset.add_tool(_GatedTool(tool, self))
        return toolset or None

    async def invoke(
        self,
        context: star.Context,
        event: AstrMessageEvent,
        tool_name: str,
        arguments: dict,
    ) -> str:
        """Run a registered tool outside the model's tool loop, through the same gate."""
        tool = context.get_llm_tool_manager().get_full_tool_set().get_tool(tool_name)
        if tool is None or not tool.active:
            return f"工具 {tool_name} 现在用不了。"
        run_context = ContextWrapper(context=SimpleNamespace(context=context, event=event))
        result = await _GatedTool(tool, self).call(run_context, **arguments)
        return _result_text(result)

    def take_images(self, event: AstrMessageEvent) -> list[str]:
        images = event.get_extra(_IMAGES_EXTRA_KEY) or []
        event.set_extra(_IMAGES_EXTRA_KEY, [])
        return images
