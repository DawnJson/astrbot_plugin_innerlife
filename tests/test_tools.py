from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mcp.types import CallToolResult, TextContent

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.provider.func_tool_manager import FunctionToolManager


PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))

from tools import DEFAULT_TOOL_BLOCKED_TERMS, ToolGate  # noqa: E402


class FakeEvent:
    def __init__(self) -> None:
        self.extras: dict = {}

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value) -> None:
        self.extras[key] = value


class FakeTool(FunctionTool):
    def __init__(self, name: str, reply) -> None:
        super().__init__(name=name, description="d", parameters={"type": "object", "properties": {}})
        self.reply = reply
        self.calls: list[dict] = []

    async def call(self, context, **kwargs):
        self.calls.append(kwargs)
        return self.reply


def text_result(text: str, is_error: bool = False) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], isError=is_error)


class ToolGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        (self.dir / "images").mkdir()
        self.usage = self.dir / "usage.json"
        self.manager = FunctionToolManager()
        self.context = SimpleNamespace(get_llm_tool_manager=lambda: self.manager)
        self.event = FakeEvent()
        self.run_ctx = ContextWrapper(context=SimpleNamespace(event=self.event))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def gate(self, limits=None, terms=("血腥",)) -> ToolGate:
        return ToolGate(terms, limits or {}, self.usage)

    def run_tool(self, toolset, name, **kwargs):
        return asyncio.run(toolset.get_tool(name).call(self.run_ctx, **kwargs))

    def test_toolset_only_contains_allowed_registered_tools(self) -> None:
        self.manager.func_list += [FakeTool("a", "x"), FakeTool("b", "y")]
        gate = self.gate()
        self.assertEqual(gate.toolset_for(self.context, ["a", "missing"]).names(), ["a"])
        self.assertIsNone(gate.toolset_for(self.context, ["missing"]))
        self.assertIsNone(gate.toolset_for(self.context, []))

    def test_registered_tool_objects_are_not_mutated(self) -> None:
        tool = FakeTool("a", "x")
        self.manager.func_list.append(tool)
        toolset = self.gate().toolset_for(self.context, ["a"])
        self.assertIsNot(toolset.get_tool("a"), tool)
        self.assertIs(self.manager.func_list[0], tool)
        self.assertEqual(self.run_tool(toolset, "a", q="hi"), "x")
        self.assertEqual(tool.calls, [{"q": "hi"}])

    def test_handler_based_plugin_tool_is_called_with_event(self) -> None:
        async def handler(event, q: str):
            return f"{type(event).__name__}:{q}"

        self.manager.func_list.append(
            FunctionTool(
                name="h",
                description="d",
                parameters={"type": "object", "properties": {}},
                handler=handler,
            )
        )
        toolset = self.gate().toolset_for(self.context, ["h"])
        self.assertEqual(self.run_tool(toolset, "h", q="hi"), "FakeEvent:hi")

    def test_blocked_terms_refuse_without_calling(self) -> None:
        tool = FakeTool("a", "x")
        self.manager.func_list.append(tool)
        toolset = self.gate(terms=["血腥", "Gore"]).toolset_for(self.context, ["a"])
        for args in (
            {"prompt": "画一个 血 腥 场面"},
            {"prompt": "ＧＯＲＥ scene"},
            {"nested": {"x": ["ok", "go re"]}},
        ):
            with self.subTest(args=args):
                self.assertIn("拒绝", self.run_tool(toolset, "a", **args))
        self.assertEqual(self.run_tool(toolset, "a", prompt="一只猫", n=3), "x")
        self.assertEqual(tool.calls, [{"prompt": "一只猫", "n": 3}])

    def test_default_blocked_terms(self) -> None:
        gate = self.gate(terms=DEFAULT_TOOL_BLOCKED_TERMS)
        for text in ("Donald  Trump 的肖像", "画习近平", "NSFW 图", "sheer gore", "r18 漫画"):
            with self.subTest(blocked=text):
                self.assertTrue(gate.is_blocked(text))
        for text in ("一只在海边晒太阳的猫", "Hololive 同人", "computing center", "a global crisis"):
            with self.subTest(allowed=text):
                self.assertFalse(gate.is_blocked(text))

    def test_daily_limit_persists_and_resets_next_day(self) -> None:
        tool = FakeTool("a", "x")
        self.manager.func_list.append(tool)
        toolset = self.gate({"a": 2}).toolset_for(self.context, ["a"])
        self.assertEqual(self.run_tool(toolset, "a"), "x")
        self.assertEqual(self.run_tool(toolset, "a"), "x")
        self.assertIn("次数", self.run_tool(toolset, "a"))
        self.assertEqual(len(tool.calls), 2)

        reloaded = self.gate({"a": 2}).toolset_for(self.context, ["a"])
        self.assertIn("次数", self.run_tool(reloaded, "a"))

        data = json.loads(self.usage.read_text("utf-8"))
        data["date"] = "2000-01-01"
        self.usage.write_text(json.dumps(data), "utf-8")
        fresh = self.gate({"a": 2}).toolset_for(self.context, ["a"])
        self.assertEqual(self.run_tool(fresh, "a"), "x")
        self.assertEqual(json.loads(self.usage.read_text("utf-8"))["counts"], {"a": 1})
        self.assertFalse(self.usage.with_name("usage.json.tmp").exists())

    def test_error_results_do_not_use_quota(self) -> None:
        self.manager.func_list.append(FakeTool("a", text_result("auth failed", True)))
        toolset = self.gate({"a": 1}).toolset_for(self.context, ["a"])
        for _ in range(3):
            self.assertTrue(self.run_tool(toolset, "a").isError)

    def test_images_recorded_only_from_allowed_dir(self) -> None:
        good = (self.dir / "images" / "a.png").resolve()
        good.write_bytes(b"x")
        outside = self.dir / "b.png"
        outside.write_bytes(b"x")
        reply = text_result(
            f"saved: {good} (1 KB)\nsaved: {outside} (1 KB)\n"
            f"saved: /etc/passwd (1 KB)\nsaved: {self.dir / 'images' / 'gone.png'} (1 KB)\n"
            f"saved: {self.dir / 'images' / '..' / 'b.png'} (1 KB)"
        )
        self.manager.func_list.append(FakeTool("a", reply))
        gate = self.gate()
        toolset = gate.toolset_for(self.context, ["a"])
        self.run_tool(toolset, "a")
        self.assertEqual(gate.take_images(self.event), [str(good)])
        self.assertEqual(gate.take_images(self.event), [])

    def test_invoke_applies_the_same_gate_and_records_images(self) -> None:
        good = (self.dir / "images" / "a.png").resolve()
        good.write_bytes(b"x")
        tool = FakeTool("a", text_result(f"saved: {good} (1 KB)"))
        self.manager.func_list.append(tool)
        gate = self.gate({"a": 1})

        def invoke(name, **args):
            return asyncio.run(gate.invoke(self.context, self.event, name, args))

        self.assertIn("拒绝", invoke("a", prompt="血腥"))
        self.assertEqual(tool.calls, [])
        self.assertEqual(invoke("a", prompt="猫", quality="low"), f"saved: {good} (1 KB)")
        self.assertEqual(tool.calls, [{"prompt": "猫", "quality": "low"}])
        self.assertEqual(gate.take_images(self.event), [str(good)])
        self.assertIn("次数", invoke("a", prompt="猫"))
        self.assertIn("用不了", invoke("missing"))
        tool.active = False
        self.assertIn("用不了", invoke("a", prompt="猫"))

    def test_images_from_plain_string_results(self) -> None:
        good = (self.dir / "images" / "a.webp").resolve()
        good.write_bytes(b"x")
        self.manager.func_list.append(FakeTool("a", f"saved: {good} (1 KB)"))
        gate = self.gate()
        self.run_tool(gate.toolset_for(self.context, ["a"]), "a")
        self.assertEqual(gate.take_images(self.event), [str(good)])
        self.assertEqual(gate.take_images(FakeEvent()), [])


if __name__ == "__main__":
    unittest.main()
