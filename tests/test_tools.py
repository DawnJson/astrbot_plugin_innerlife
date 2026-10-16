from __future__ import annotations

import asyncio
import base64
import datetime
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import support

if not support.HAS_ASTRBOT:
    raise unittest.SkipTest("needs AstrBot: run inside the AstrBot container")

from mcp.types import CallToolResult, ImageContent, TextContent

from astrbot.core.agent.tool import FunctionTool
from astrbot.core.provider.func_tool_manager import FunctionToolManager

from astrbot_plugin_innerlife.store import Store
from astrbot_plugin_innerlife.tools import BUILTIN_BLOCKED_TERMS, ToolGate

PNG = b"\x89PNG\r\n\x1a\n-test-image"
UTC = datetime.timezone.utc


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


def image_content(data: bytes = PNG, mime: str = "image/png") -> ImageContent:
    return ImageContent(type="image", data=base64.b64encode(data).decode(), mimeType=mime)


class ToolGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.images = self.dir / "images"
        self.images.mkdir()
        self.store = Store(self.dir / "innerlife.db")
        self.manager = FunctionToolManager()
        self.context = SimpleNamespace(get_llm_tool_manager=lambda: self.manager)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def gate(self, limit: int = 5, terms=("血腥",)) -> ToolGate:
        return ToolGate(terms, limit, self.store, self.images, UTC)

    def invoke(self, gate: ToolGate, name: str = "a", event=None, **arguments) -> list[str]:
        return asyncio.run(gate.invoke(self.context, event, name, arguments))

    def tool(self, reply, name: str = "a") -> FakeTool:
        tool = FakeTool(name, reply)
        self.manager.func_list.append(tool)
        return tool

    def test_blocked_terms_refuse_without_calling(self) -> None:
        tool = self.tool(text_result("done"))
        gate = self.gate(terms=["血腥", "Gore"])
        for arguments in (
            {"prompt": "画一个 血 腥 场面"},
            {"prompt": "ＧＯＲＥ scene"},
            {"nested": {"x": ["ok", "go re"]}},
        ):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.invoke(gate, **arguments), [])
        self.assertEqual(tool.calls, [])
        self.invoke(gate, prompt="一只猫", n=3)
        self.assertEqual(tool.calls, [{"prompt": "一只猫", "n": 3}])

    def test_builtin_and_schema_default_terms(self) -> None:
        gate = self.gate(terms=[*BUILTIN_BLOCKED_TERMS, *support.make_settings().draw_blocked_terms])
        for text in ("Donald  Trump 的肖像", "画习近平", "NSFW 图", "sheer gore", "r18 漫画"):
            with self.subTest(blocked=text):
                self.assertTrue(gate.is_blocked(text))
        for text in ("一只在海边晒太阳的猫", "Hololive 同人", "computing center", "a global crisis"):
            with self.subTest(allowed=text):
                self.assertFalse(gate.is_blocked(text))
        self.assertFalse(self.gate(terms=BUILTIN_BLOCKED_TERMS).is_blocked("画习近平"))

    def test_daily_limit_lives_in_the_db_and_resets_next_day(self) -> None:
        tool = self.tool(text_result("done"))
        gate = self.gate(limit=2)
        for _ in range(3):
            self.invoke(gate, prompt="猫")
        self.assertEqual(len(tool.calls), 2)
        self.invoke(self.gate(limit=2), prompt="猫")
        self.assertEqual(len(tool.calls), 2)

        tomorrow = (datetime.datetime.now(UTC) + datetime.timedelta(days=1)).date().isoformat()
        gate._today = lambda: tomorrow
        self.invoke(gate, prompt="猫")
        self.assertEqual(len(tool.calls), 3)
        self.assertEqual(self.store.tool_uses(tomorrow, "a"), 1)
        self.assertEqual(self.store.tool_uses(datetime.datetime.now(UTC).date().isoformat(), "a"), 0)

    def test_error_results_do_not_use_quota(self) -> None:
        tool = self.tool(text_result("auth failed", True))
        gate = self.gate(limit=1)
        for _ in range(3):
            self.assertEqual(self.invoke(gate), [])
        self.assertEqual(len(tool.calls), 3)

    def test_unavailable_tools_give_nothing(self) -> None:
        tool = self.tool(text_result("done"))
        gate = self.gate()
        self.assertEqual(self.invoke(gate, "missing"), [])
        tool.active = False
        self.assertEqual(self.invoke(gate, prompt="猫"), [])
        self.assertEqual(tool.calls, [])

    def test_image_content_is_decoded_into_the_image_dir(self) -> None:
        self.tool(CallToolResult(content=[TextContent(type="text", text="ok"), image_content()]))
        [path] = self.invoke(self.gate(), prompt="猫")
        saved = Path(path)
        self.assertEqual(saved.parent, self.images.resolve())
        self.assertEqual(saved.suffix, ".png")
        self.assertEqual(saved.read_bytes(), PNG)

    def test_every_image_content_counts_and_other_mime_types_do_not(self) -> None:
        self.tool(
            CallToolResult(
                content=[
                    image_content(b"one", "image/jpeg"),
                    image_content(b"two", "image/webp"),
                    image_content(b"<svg/>", "image/svg+xml"),
                ]
            )
        )
        paths = self.invoke(self.gate(), prompt="猫")
        self.assertEqual([Path(p).suffix for p in paths], [".jpg", ".webp"])
        self.assertEqual([Path(p).read_bytes() for p in paths], [b"one", b"two"])

    def test_image_paths_in_the_text_count_only_inside_the_image_dir(self) -> None:
        good = (self.images / "a.png").resolve()
        good.write_bytes(b"x")
        outside = self.dir / "b.png"
        outside.write_bytes(b"x")
        self.tool(
            text_result(
                f"saved: {good} (1 KB)\nsaved: {outside} (1 KB)\n"
                f"saved: /etc/passwd.png (1 KB)\nsaved: {self.images / 'gone.png'} (1 KB)\n"
                f"saved: {self.images / '..' / 'b.png'} (1 KB)\nsaved: {good.with_suffix('.txt')}"
            )
        )
        self.assertEqual(self.invoke(self.gate(), prompt="猫"), [str(good)])

    def test_the_path_is_found_without_the_saved_prefix(self) -> None:
        good = (self.images / "pic.webp").resolve()
        good.write_bytes(b"x")
        self.tool(text_result(f"Image written to {good}."))
        self.assertEqual(self.invoke(self.gate(), prompt="猫"), [str(good)])

    def test_plain_string_results_work_and_repeated_paths_count_once(self) -> None:
        good = (self.images / "a.jpeg").resolve()
        good.write_bytes(b"x")
        self.tool(f"{good} and again {good}")
        self.assertEqual(self.invoke(self.gate(), prompt="猫"), [str(good)])

    def test_image_content_wins_over_a_path_to_the_same_picture(self) -> None:
        good = (self.images / "a.png").resolve()
        good.write_bytes(PNG)
        self.tool(CallToolResult(content=[TextContent(type="text", text=f"saved: {good}"), image_content()]))
        paths = self.invoke(self.gate(), prompt="猫")
        self.assertEqual(len(paths), 1)
        self.assertNotEqual(paths[0], str(good))

    def test_a_tool_that_needs_the_event_gets_it(self) -> None:
        seen = []

        async def handler(event, q: str):
            seen.append((type(event).__name__, q))
            return "done"

        self.manager.func_list.append(
            FunctionTool(
                name="h",
                description="d",
                parameters={"type": "object", "properties": {}},
                handler=handler,
            )
        )

        class FakeEvent:
            pass

        self.invoke(self.gate(), "h", FakeEvent(), q="hi")
        self.assertEqual(seen, [("FakeEvent", "hi")])


if __name__ == "__main__":
    unittest.main()
