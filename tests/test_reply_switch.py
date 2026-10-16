from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))

from reply_switch import (  # noqa: E402
    ReplySwitch,
    canonical_reply_switch_command,
    is_group_manager,
)


class ReplySwitchCommandTests(unittest.TestCase):
    def test_commands_are_exact_and_whitespace_tolerant(self) -> None:
        self.assertEqual(canonical_reply_switch_command("/机器人  开启"), "enable")
        self.assertEqual(canonical_reply_switch_command("／机器人　关闭"), "disable")
        self.assertIsNone(canonical_reply_switch_command("机器人 关闭"))
        self.assertIsNone(canonical_reply_switch_command("/机器人"))
        self.assertIsNone(canonical_reply_switch_command("请关闭机器人"))
        self.assertIsNone(canonical_reply_switch_command("/机器人 开启 全部"))

    def test_command_word_is_configurable(self) -> None:
        self.assertEqual(canonical_reply_switch_command("/小助手 开启", "/小助手"), "enable")
        self.assertEqual(canonical_reply_switch_command("/小助手  关闭", "/小助手"), "disable")
        self.assertIsNone(canonical_reply_switch_command("/机器人 开启", "/小助手"))

    def test_only_owner_and_admin_are_managers(self) -> None:
        self.assertTrue(is_group_manager({"sender": {"role": "Owner"}}))
        self.assertTrue(is_group_manager({"sender": {"role": "admin"}}))
        self.assertFalse(is_group_manager({"sender": {"role": "member"}}))
        self.assertFalse(is_group_manager(None))


class ReplySwitchStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_state_is_per_group_and_survives_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_path = Path(temporary_directory) / "reply_switch.json"
            switch = ReplySwitch(state_path)
            self.assertTrue(switch.is_enabled("10001"))
            self.assertTrue(switch.is_enabled("10002"))

            disabled = await switch.set_enabled("10001", False)
            self.assertTrue(disabled.changed)
            self.assertFalse(disabled.enabled)
            self.assertFalse(switch.is_enabled("10001"))
            self.assertTrue(switch.is_enabled("10002"))
            if os.name == "posix":
                self.assertEqual(state_path.stat().st_mode & 0o777, 0o600)

            unchanged = await switch.set_enabled("10001", False)
            self.assertFalse(unchanged.changed)
            reloaded = ReplySwitch(state_path)
            self.assertFalse(reloaded.is_enabled("10001"))
            self.assertTrue(reloaded.is_enabled("10002"))

            enabled = await reloaded.set_enabled("10001", True)
            self.assertTrue(enabled.changed)
            self.assertTrue(ReplySwitch(state_path).is_enabled("10001"))
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["disabled_group_ids"], [])

    async def test_failed_write_rolls_back_memory_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            switch = ReplySwitch(Path(temporary_directory) / "reply_switch.json")

            def fail_write() -> None:
                raise OSError("simulated write failure")

            switch._write_state = fail_write
            with self.assertRaises(OSError):
                await switch.set_enabled("10001", False)
            self.assertTrue(switch.is_enabled("10001"))

    async def test_invalid_state_is_ignored_without_overwriting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_path = Path(temporary_directory) / "reply_switch.json"
            state_path.write_text("not json", encoding="utf-8")
            switch = ReplySwitch(state_path)
            self.assertEqual(switch.load_warning, "JSONDecodeError")
            self.assertTrue(switch.is_enabled("10001"))
            self.assertEqual(state_path.read_text(encoding="utf-8"), "not json")

    def test_invalid_group_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            switch = ReplySwitch(Path(temporary_directory) / "reply_switch.json")
            with self.assertRaises(ValueError):
                switch.is_enabled("../../etc")


if __name__ == "__main__":
    unittest.main()
