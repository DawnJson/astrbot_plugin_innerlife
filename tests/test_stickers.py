from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import support  # noqa: F401  (puts the plugin's parent on sys.path)
from astrbot_plugin_innerlife.marker import marker_rules, parse_marker
from astrbot_plugin_innerlife.stickers import PROMPT_STICKERS, STICKER_SIDE, Stickers, image_ext
from astrbot_plugin_innerlife.store import Store

FORMATS = {"png": "PNG", "jpg": "JPEG", "gif": "GIF", "webp": "WEBP"}


def image(ext: str, tag: int, size: tuple[int, int] = (8, 8), frames: int = 1, noise: bool = False) -> bytes:
    shots = [
        Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
        if noise
        else Image.new("RGB", size, (tag % 256, tag // 256, 60 * n))
        for n in range(frames)
    ]
    out = io.BytesIO()
    shots[0].save(out, FORMATS[ext], save_all=frames > 1, append_images=shots[1:])
    return out.getvalue()


class StickerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.store = Store(root / "innerlife.db")
        self.stickers = Stickers(self.store, root / "stickers", max_count=3, max_bytes=4096)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def kept(self, row) -> Image.Image:
        return Image.open(self.stickers.directory / row["file"])

    def test_format_comes_from_the_bytes(self) -> None:
        self.assertEqual(
            [image_ext(image(ext, 1)) for ext in FORMATS], ["png", "jpg", "gif", "webp"]
        )
        self.assertIsNone(image_ext(b"<svg></svg>"))
        self.assertIsNone(image_ext(b"RIFF\x00\x00\x00\x00WAVE"))
        self.assertFalse(self.stickers.save(b"<svg></svg>" + b"x" * 20, "矢量图", 1.0))
        # A known header over bytes that are not an image is not kept either.
        self.assertFalse(self.stickers.save(b"\x89PNG\r\n\x1a\n" + b"x" * 20, "坏图", 1.0))
        self.assertTrue(self.stickers.save(image("webp", 1), "歪着头", 1.0))
        [row] = self.store.all_stickers()
        self.assertTrue(row["file"].endswith(".webp"))
        # Already small: kept byte for byte.
        self.assertEqual(
            (self.stickers.directory / row["file"]).read_bytes(), image("webp", 1)
        )

    def test_big_stickers_are_shrunk_and_animation_kept(self) -> None:
        photo = image("jpg", 1, size=(960, 480))
        moving = image("gif", 2, size=(480, 480), frames=3)
        roomy = Stickers(self.store, self.stickers.directory, max_count=3, max_bytes=1 << 20)
        self.assertTrue(roomy.save(photo, "大图", 1.0))
        self.assertTrue(roomy.save(moving, "动图", 1.0))
        flat, animated = self.store.all_stickers()
        self.assertEqual(self.kept(flat).size, (STICKER_SIDE, STICKER_SIDE // 2))
        self.assertEqual(self.kept(flat).format, "JPEG")
        self.assertEqual(self.kept(animated).size, (STICKER_SIDE, STICKER_SIDE))
        self.assertEqual(self.kept(animated).n_frames, 3)
        # The same image sent again is still known, though the file holds the small copy.
        self.assertFalse(roomy.save(photo, "同一张大图", 2.0))

    def test_too_big_and_duplicates_are_skipped(self) -> None:
        self.assertFalse(self.stickers.save(image("png", 1, size=(64, 64), noise=True), "太大", 1.0))
        self.assertTrue(self.stickers.save(image("png", 2), "无语", 1.0))
        self.assertFalse(self.stickers.save(image("png", 2), "同一张", 2.0))
        self.assertEqual([row["description"] for row in self.store.all_stickers()], ["无语"])
        self.assertEqual(len(list(self.stickers.directory.iterdir())), 1)

    def test_over_the_limit_the_least_used_oldest_goes(self) -> None:
        for tag in range(3):
            self.assertTrue(self.stickers.save(image("png", tag), f"图{tag}", float(tag)))
        first, second, third = self.store.all_stickers()
        self.store.use_sticker(first["id"], 10.0)
        self.assertTrue(self.stickers.save(image("jpg", 9), "新图", 5.0))
        rows = self.store.all_stickers()
        self.assertEqual([row["description"] for row in rows], ["图0", "图2", "新图"])
        self.assertFalse((self.stickers.directory / second["file"]).exists())
        self.assertTrue((self.stickers.directory / third["file"]).exists())
        self.assertEqual(len(list(self.stickers.directory.iterdir())), 3)

    def test_listing_and_taking(self) -> None:
        self.assertEqual(self.stickers.listing(), "")
        self.stickers.save(image("gif", 1), "开心转圈", 1.0)
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
        big = Stickers(self.store, self.stickers.directory, max_count=100, max_bytes=4096)
        for tag in range(PROMPT_STICKERS + 20):
            big.save(image("png", tag), f"图{tag}", float(tag))
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
