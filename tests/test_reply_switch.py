from __future__ import annotations

import sys
import unittest
from pathlib import Path


PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))

from reply_switch import (  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
