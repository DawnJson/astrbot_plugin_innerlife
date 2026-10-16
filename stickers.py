"""Reaction images she keeps from the group and sends later: files plus the `stickers` table."""

from __future__ import annotations

import hashlib
import random
from pathlib import Path

from .store import Store

PROMPT_STICKERS = 40
PROMPT_MOST_USED = 20  # the rest of the list is random, so what she picks varies


def image_ext(data: bytes) -> str | None:
    """The file extension for png / jpg / gif / webp judged by the real bytes, else None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


class Stickers:
    def __init__(self, store: Store, directory: Path, max_count: int, max_bytes: int) -> None:
        self.store = store
        self.directory = directory
        self.max_count = max_count
        self.max_bytes = max_bytes
        directory.mkdir(parents=True, exist_ok=True)

    def save(self, data: bytes, description: str, now: float) -> bool:
        """Keep an image; False when it is not an allowed format, too big, or already kept.
        Over the count limit the least used, oldest sticker is deleted first."""
        ext = image_ext(data)
        if ext is None or len(data) > self.max_bytes:
            return False
        sha256 = hashlib.sha256(data).hexdigest()
        if self.store.sticker_known(sha256):
            return False
        while len(self.store.all_stickers()) >= self.max_count:
            self._delete(self.store.least_used_sticker())
        file = f"{sha256}.{ext}"
        (self.directory / file).write_bytes(data)
        self.store.add_sticker(sha256, file, description, now)
        return True

    def _delete(self, row) -> None:
        self.store.delete_sticker(row["id"])
        (self.directory / row["file"]).unlink(missing_ok=True)

    def listing(self) -> str:
        """Up to PROMPT_STICKERS lines `id. description`, empty when she has none."""
        rows = sorted(self.store.all_stickers(), key=lambda row: -row["used_count"])
        rest = rows[PROMPT_MOST_USED:]
        chosen = rows[:PROMPT_MOST_USED] + random.sample(
            rest, min(len(rest), PROMPT_STICKERS - PROMPT_MOST_USED)
        )
        chosen.sort(key=lambda row: row["id"])
        return "\n".join(f"{row['id']}. {row['description']}" for row in chosen)

    def take(self, sticker_id: int) -> tuple[Path, str] | None:
        """The file and description of a sticker she wants to send; a row whose file is gone is dropped."""
        row = self.store.sticker(sticker_id)
        if row is None:
            return None
        path = self.directory / row["file"]
        if not path.is_file():
            self.store.delete_sticker(sticker_id)
            return None
        return path, row["description"]
