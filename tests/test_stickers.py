from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401  (puts the plugin's parent on sys.path)
from astrbot_plugin_innerlife.marker import marker_rules, parse_marker
from astrbot_plugin_innerlife.stickers import PROMPT_STICKERS, Stickers, image_ext
from astrbot_plugin_innerlife.store import Store

PNG = b"\x89PNG\r\n\x1a\n"
JPG = b"\xff\xd8\xff\xe0"
GIF = b"GIF89a"
WEBP = b"RIFF\x00\x00\x00\x00WEBP"


def image(kind: bytes, tag: int, size: int = 32) -> bytes:
    return kind + tag.to_bytes(4, "big") + b"x" * size


class StickerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.store = Store(root / "innerlife.db")
        self.stickers = Stickers(self.store, root / "stickers", max_count=3, max_bytes=100)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_format_comes_from_the_bytes(self) -> None:
        self.assertEqual(
            [image_ext(kind) for kind in (PNG, JPG, GIF, WEBP)], ["png", "jpg", "gif", "webp"]
        )
        self.assertIsNone(image_ext(b"<svg></svg>"))
        self.assertIsNone(image_ext(b"RIFF\x00\x00\x00\x00WAVE"))
        self.assertFalse(self.stickers.save(b"<svg></svg>" + b"x" * 20, "矢量图", 1.0))
        self.assertTrue(self.stickers.save(image(WEBP, 1), "歪着头", 1.0))
        [row] = self.store.all_stickers()
        self.assertTrue(row["file"].endswith(".webp"))
        self.assertEqual(
            (self.stickers.directory / row["file"]).read_bytes(), image(WEBP, 1)
        )

    def test_too_big_and_duplicates_are_skipped(self) -> None:
        self.assertFalse(self.stickers.save(image(PNG, 1, size=500), "太大", 1.0))
        self.assertTrue(self.stickers.save(image(PNG, 2), "无语", 1.0))
        self.assertFalse(self.stickers.save(image(PNG, 2), "同一张", 2.0))
        self.assertEqual([row["description"] for row in self.store.all_stickers()], ["无语"])
        self.assertEqual(len(list(self.stickers.directory.iterdir())), 1)

    def test_over_the_limit_the_least_used_oldest_goes(self) -> None:
        for tag in range(3):
            self.assertTrue(self.stickers.save(image(PNG, tag), f"图{tag}", float(tag)))
        first, second, third = self.store.all_stickers()
        self.store.use_sticker(first["id"], 10.0)
        self.assertTrue(self.stickers.save(image(JPG, 9), "新图", 5.0))
        rows = self.store.all_stickers()
        self.assertEqual([row["description"] for row in rows], ["图0", "图2", "新图"])
        self.assertFalse((self.stickers.directory / second["file"]).exists())
        self.assertTrue((self.stickers.directory / third["file"]).exists())
        self.assertEqual(len(list(self.stickers.directory.iterdir())), 3)

    def test_listing_and_taking(self) -> None:
        self.assertEqual(self.stickers.listing(), "")
        self.stickers.save(image(GIF, 1), "开心转圈", 1.0)
        [row] = self.store.all_stickers()
        self.assertEqual(self.stickers.listing(), f"{row['id']}. 开心转圈")
        path, description = self.stickers.take(row["id"])
        self.assertEqual((path.name, description), (row["file"], "开心转圈"))
        self.assertIsNone(self.stickers.take(row["id"] + 1))
        # The file disappeared: the row goes too.
        path.unlink()
        self.assertIsNone(self.stickers.take(row["id"]))
        self.assertEqual(self.store.all_stickers(), [])

    def test_listing_is_capped_and_keeps_the_most_used(self) -> None:
        big = Stickers(self.store, self.stickers.directory, max_count=100, max_bytes=100)
        for tag in range(PROMPT_STICKERS + 20):
            big.save(image(PNG, tag), f"图{tag}", float(tag))
        rows = self.store.all_stickers()
        favourite = rows[-1]
        for _ in range(5):
            self.store.use_sticker(favourite["id"], 1.0)
        lines = big.listing().splitlines()
        self.assertEqual(len(lines), PROMPT_STICKERS)
        self.assertIn(f"{favourite['id']}. {favourite['description']}", lines)


class MarkerTests(unittest.TestCase):
    def test_keep_and_sticker_are_parsed(self) -> None:
        text, marker = parse_marker(
            '好笑\n<il>{"keep":[{"i":1,"d":"无语时用"},{"i":2,"d":"开心"},{"i":3,"d":"第三张"},'
            '{"i":0,"d":"零号"},{"i":"1","d":"字符串"},{"i":2},"junk"],"s":7}</il>'
        )
        self.assertEqual(text, "好笑")
        self.assertEqual([(k.image, k.description) for k in marker.keep], [(1, "无语时用"), (2, "开心")])
        self.assertEqual(marker.sticker, 7)
        for bad in ('{"s":"7"}', '{"s":true}', '{"s":null}', '{"keep":"x"}'):
            _, parsed = parse_marker(f"嗯<il>{bad}</il>")
            self.assertIsNone(parsed.sticker)
            self.assertEqual(parsed.keep, ())

    def test_rules_teach_only_what_can_be_used(self) -> None:
        plain = marker_rules(emotion=False, memory=False, draw=False)
        self.assertEqual(plain, "")
        keep = marker_rules(emotion=False, memory=False, draw=False, keep=True)
        self.assertIn("keep：", keep)
        self.assertIn("真人照片", keep)
        self.assertNotIn("s：", keep)
        send = marker_rules(emotion=False, memory=False, draw=False, stickers="3. 无语")
        self.assertIn("s：", send)
        self.assertIn("3. 无语", send)
        self.assertNotIn("keep：", send)


if __name__ == "__main__":
    unittest.main()
