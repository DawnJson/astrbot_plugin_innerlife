from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import closing, contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
  user_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  affinity REAL NOT NULL DEFAULT 0,
  impression TEXT NOT NULL DEFAULT '',
  last_interaction REAL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY,
  user_id TEXT NOT NULL,
  group_id TEXT NOT NULL,
  content TEXT NOT NULL,
  private INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'draft',
  strength REAL NOT NULL DEFAULT 1,
  mentions INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL,
  last_mentioned REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS diaries (
  kind TEXT NOT NULL,
  period TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at REAL NOT NULL,
  edited_at REAL,
  PRIMARY KEY (kind, period)
);
CREATE TABLE IF NOT EXISTS mood (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  valence REAL NOT NULL,
  arousal REAL NOT NULL,
  energy REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS emotions (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  target_user_id TEXT,
  intensity REAL NOT NULL,
  cause TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  held REAL NOT NULL DEFAULT 1              -- decay stretch factor (grudges), written by innerlife only
);
CREATE TABLE IF NOT EXISTS life (
  day TEXT PRIMARY KEY,
  schedule TEXT NOT NULL DEFAULT '',
  weather TEXT NOT NULL DEFAULT '',
  day_kind TEXT NOT NULL DEFAULT '',
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS news (
  link TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  published_at REAL,
  fetched_at REAL NOT NULL,
  used_at REAL
);
CREATE TABLE IF NOT EXISTS chat_log (
  id INTEGER PRIMARY KEY,
  group_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  name TEXT NOT NULL,
  text TEXT NOT NULL,
  at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS memories_user ON memories (user_id);
CREATE INDEX IF NOT EXISTS emotions_created ON emotions (created_at);
CREATE INDEX IF NOT EXISTS chat_log_at ON chat_log (at);
"""


class Store:
    """innerlife.db: every read goes to the file, so edits made from outside show up at once."""

    def __init__(self, path: Path) -> None:
        self.path = path
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            if "held" not in {row[1] for row in db.execute("PRAGMA table_info(emotions)")}:
                db.execute("ALTER TABLE emotions ADD COLUMN held REAL NOT NULL DEFAULT 1")

    @contextmanager
    def _db(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    def _all(self, sql: str, args: Iterable = ()) -> list[sqlite3.Row]:
        with self._db() as db:
            return db.execute(sql, tuple(args)).fetchall()

    def _one(self, sql: str, args: Iterable = ()) -> sqlite3.Row | None:
        with self._db() as db:
            return db.execute(sql, tuple(args)).fetchone()

    def _run(self, sql: str, args: Iterable = ()) -> int:
        with self._db() as db:
            return db.execute(sql, tuple(args)).lastrowid

    # people
    def person(self, user_id: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM people WHERE user_id=?", (user_id,))

    def people(self, user_ids: list[str]) -> list[sqlite3.Row]:
        marks = ",".join("?" * len(user_ids))
        return self._all(f"SELECT * FROM people WHERE user_id IN ({marks})", user_ids)

    def save_person(
        self, user_id: str, name: str, affinity: float, impression: str, now: float
    ) -> None:
        self._run(
            "INSERT INTO people (user_id,name,affinity,impression,last_interaction,updated_at)"
            " VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET name=excluded.name,"
            " affinity=excluded.affinity, impression=excluded.impression,"
            " last_interaction=excluded.last_interaction, updated_at=excluded.updated_at",
            (user_id, name, affinity, impression, now, now),
        )

    def touch_person(self, user_id: str, name: str, now: float) -> None:
        self._run(
            "INSERT INTO people (user_id,name,last_interaction,updated_at) VALUES (?,?,?,?)"
            " ON CONFLICT(user_id) DO UPDATE SET name=excluded.name,"
            " last_interaction=excluded.last_interaction",
            (user_id, name, now, now),
        )

    def all_people(self) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM people ORDER BY user_id")

    def set_impression(self, user_id: str, impression: str, now: float) -> None:
        self._run(
            "UPDATE people SET impression=?, updated_at=? WHERE user_id=?",
            (impression, now, user_id),
        )

    def forget_person(self, user_id: str) -> None:
        """Drop everything kept about one member except diary text she already wrote."""
        with self._db() as db:
            db.execute("DELETE FROM people WHERE user_id=?", (user_id,))
            db.execute("DELETE FROM memories WHERE user_id=?", (user_id,))
            db.execute("DELETE FROM emotions WHERE target_user_id=?", (user_id,))
            db.execute("DELETE FROM chat_log WHERE user_id=?", (user_id,))

    def quiet_people(self, before: float) -> list[str]:
        """Members she has not talked with since `before`."""
        rows = self._all(
            "SELECT user_id FROM people WHERE COALESCE(last_interaction, updated_at)<?",
            (before,),
        )
        return [row["user_id"] for row in rows]

    # memories
    def add_memory(
        self, user_id: str, group_id: str, content: str, private: bool, now: float
    ) -> int:
        return self._run(
            "INSERT INTO memories (user_id,group_id,content,private,created_at,last_mentioned)"
            " VALUES (?,?,?,?,?,?)",
            (user_id, group_id, content, int(private), now, now),
        )

    def memories_of(self, user_id: str, *, private: bool | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM memories WHERE user_id=?"
        if private is not None:
            sql += f" AND private={int(private)}"
        return self._all(sql + " ORDER BY id", (user_id,))

    def all_memories(self) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM memories ORDER BY id")

    def drafts(self) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM memories WHERE status='draft' ORDER BY id")

    def reinforce_memory(self, memory_id: int, strength: float, now: float) -> None:
        self._run(
            "UPDATE memories SET strength=?, mentions=mentions+1, last_mentioned=? WHERE id=?",
            (strength, now, memory_id),
        )

    def update_memory(
        self, memory_id: int, content: str, private: bool, status: str
    ) -> None:
        self._run(
            "UPDATE memories SET content=?, private=?, status=? WHERE id=?",
            (content, int(private), status, memory_id),
        )

    def delete_memories(self, ids: list[int]) -> None:
        with self._db() as db:
            db.executemany("DELETE FROM memories WHERE id=?", [(i,) for i in ids])

    # diaries
    def diary(self, kind: str, period: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM diaries WHERE kind=? AND period=?", (kind, period))

    def save_diary(self, kind: str, period: str, body: str, now: float) -> None:
        self._run(
            "INSERT INTO diaries (kind,period,body,created_at) VALUES (?,?,?,?)"
            " ON CONFLICT(kind,period) DO UPDATE SET body=excluded.body,"
            " created_at=excluded.created_at, edited_at=NULL",
            (kind, period, body, now),
        )

    def recent_diaries(self, kind: str, limit: int) -> list[sqlite3.Row]:
        return self._all(
            "SELECT * FROM diaries WHERE kind=? ORDER BY period DESC LIMIT ?", (kind, limit)
        )

    def diaries_between(self, kind: str, first: str, last: str) -> list[sqlite3.Row]:
        return self._all(
            "SELECT * FROM diaries WHERE kind=? AND period>=? AND period<=? ORDER BY period",
            (kind, first, last),
        )

    def delete_diaries_before(self, kind: str, period: str) -> None:
        self._run("DELETE FROM diaries WHERE kind=? AND period<?", (kind, period))

    # mood and emotions
    def mood(self) -> sqlite3.Row | None:
        return self._one("SELECT * FROM mood WHERE id=1")

    def save_mood(self, valence: float, arousal: float, energy: float, now: float) -> None:
        self._run(
            "INSERT INTO mood (id,valence,arousal,energy,updated_at) VALUES (1,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET valence=excluded.valence, arousal=excluded.arousal,"
            " energy=excluded.energy, updated_at=excluded.updated_at",
            (valence, arousal, energy, now),
        )

    def add_emotion(
        self,
        kind: str,
        target_user_id: str | None,
        intensity: float,
        cause: str,
        now: float,
        held: float = 1.0,
    ) -> None:
        self._run(
            "INSERT INTO emotions (kind,target_user_id,intensity,cause,created_at,held)"
            " VALUES (?,?,?,?,?,?)",
            (kind, target_user_id, intensity, cause, now, held),
        )

    def emotions_since(self, since: float) -> list[sqlite3.Row]:
        return self._all(
            "SELECT * FROM emotions WHERE created_at>=? ORDER BY created_at", (since,)
        )

    def delete_emotions_before(self, before: float) -> None:
        self._run("DELETE FROM emotions WHERE created_at<?", (before,))

    # life and news
    def life(self, day: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM life WHERE day=?", (day,))

    def save_life(self, day: str, now: float, **fields: str) -> None:
        row = self.life(day)
        values = {
            "schedule": "",
            "weather": "",
            "day_kind": "",
            **({key: row[key] for key in ("schedule", "weather", "day_kind")} if row else {}),
            **fields,
        }
        self._run(
            "INSERT INTO life (day,schedule,weather,day_kind,updated_at) VALUES (?,?,?,?,?)"
            " ON CONFLICT(day) DO UPDATE SET schedule=excluded.schedule, weather=excluded.weather,"
            " day_kind=excluded.day_kind, updated_at=excluded.updated_at",
            (day, values["schedule"], values["weather"], values["day_kind"], now),
        )

    def add_news(self, items: Iterable[tuple], now: float) -> None:
        """items: (link, title, summary, source, published_at)."""
        with self._db() as db:
            db.executemany(
                "INSERT OR IGNORE INTO news (link,title,summary,source,published_at,fetched_at)"
                " VALUES (?,?,?,?,?,?)",
                [(*item, now) for item in items],
            )

    def unused_news(self, limit: int) -> list[sqlite3.Row]:
        return self._all(
            "SELECT * FROM news WHERE used_at IS NULL ORDER BY COALESCE(published_at,fetched_at) DESC"
            " LIMIT ?",
            (limit,),
        )

    def mark_news_used(self, links: list[str], now: float) -> None:
        with self._db() as db:
            db.executemany("UPDATE news SET used_at=? WHERE link=?", [(now, l) for l in links])

    def delete_news_before(self, before: float) -> None:
        self._run("DELETE FROM news WHERE fetched_at<?", (before,))

    # chat log
    def log_line(self, group_id: str, user_id: str, name: str, text: str, now: float) -> None:
        self._run(
            "INSERT INTO chat_log (group_id,user_id,name,text,at) VALUES (?,?,?,?,?)",
            (group_id, user_id, name, text, now),
        )

    def chat_log(self) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM chat_log ORDER BY at, id")

    def purge_chat_log(self, up_to: float) -> None:
        self._run("DELETE FROM chat_log WHERE at<=?", (up_to,))
