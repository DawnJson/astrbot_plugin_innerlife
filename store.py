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
CREATE TABLE IF NOT EXISTS night_jobs (
  day TEXT NOT NULL,
  step TEXT NOT NULL,                       -- consolidate / daily / weekly / monthly, then complete
  PRIMARY KEY (day, step)
);
CREATE TABLE IF NOT EXISTS tool_usage (
  day TEXT NOT NULL,
  name TEXT NOT NULL,
  count INTEGER NOT NULL,
  PRIMARY KEY (day, name)
);
CREATE TABLE IF NOT EXISTS disabled_groups (
  group_id TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS talk (
  group_id TEXT PRIMARY KEY,
  bias REAL NOT NULL,
  her REAL NOT NULL,
  total REAL NOT NULL,
  heat REAL NOT NULL,
  urge REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS talk_log (
  at REAL NOT NULL,
  group_id TEXT NOT NULL,
  kind TEXT NOT NULL,                       -- chat / topic
  p REAL,
  outcome TEXT NOT NULL,                    -- skip / PASS / CHAT / fuse / busy / fail / sent
  features TEXT NOT NULL                    -- JSON
);
CREATE TABLE IF NOT EXISTS model_calls (
  group_id TEXT NOT NULL,
  day TEXT NOT NULL,
  hour TEXT NOT NULL,
  count INTEGER NOT NULL,
  PRIMARY KEY (group_id, hour)
);
CREATE TABLE IF NOT EXISTS stickers (
  id INTEGER PRIMARY KEY,
  sha256 TEXT UNIQUE NOT NULL,
  file TEXT NOT NULL,                       -- bare file name inside the stickers directory
  description TEXT NOT NULL,
  saved_at REAL NOT NULL,
  used_count INTEGER NOT NULL DEFAULT 0,
  last_used REAL
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

    def touch_person(
        self, user_id: str, name: str, now: float, *, affinity: float | None = None
    ) -> None:
        """They spoke; `affinity` (when given) replaces the stored one, see Mind.touch."""
        self._run(
            "INSERT INTO people (user_id,name,last_interaction,updated_at) VALUES (?,?,?,?)"
            " ON CONFLICT(user_id) DO UPDATE SET name=excluded.name,"
            " last_interaction=excluded.last_interaction, affinity=COALESCE(?, affinity)",
            (user_id, name, now, now, affinity),
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

    def latest_emotion(
        self, kind: str, target_user_id: str | None, since: float
    ) -> sqlite3.Row | None:
        return self._one(
            "SELECT * FROM emotions WHERE kind=? AND target_user_id IS ? AND created_at>=?"
            " ORDER BY created_at DESC LIMIT 1",
            (kind, target_user_id, since),
        )

    def renew_emotion(
        self, emotion_id: int, intensity: float, cause: str, now: float, held: float
    ) -> None:
        self._run(
            "UPDATE emotions SET intensity=?, cause=?, created_at=?, held=? WHERE id=?",
            (intensity, cause, now, held, emotion_id),
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

    # night job progress
    def night_steps(self, day: str) -> set[str]:
        return {row["step"] for row in self._all("SELECT step FROM night_jobs WHERE day=?", (day,))}

    def mark_night_step(self, day: str, step: str) -> None:
        self._run("INSERT OR IGNORE INTO night_jobs (day,step) VALUES (?,?)", (day, step))

    def delete_night_before(self, day: str) -> None:
        self._run("DELETE FROM night_jobs WHERE day<?", (day,))

    # tool usage
    def tool_uses(self, day: str, name: str) -> int:
        row = self._one("SELECT count FROM tool_usage WHERE day=? AND name=?", (day, name))
        return row["count"] if row else 0

    def add_tool_use(self, day: str, name: str) -> None:
        """Count one use today; earlier days are dropped."""
        with self._db() as db:
            db.execute("DELETE FROM tool_usage WHERE day<?", (day,))
            db.execute(
                "INSERT INTO tool_usage (day,name,count) VALUES (?,?,1)"
                " ON CONFLICT(day,name) DO UPDATE SET count=count+1",
                (day, name),
            )

    # reply switch
    def group_enabled(self, group_id: str) -> bool:
        return self._one("SELECT 1 FROM disabled_groups WHERE group_id=?", (group_id,)) is None

    def set_group_enabled(self, group_id: str, enabled: bool) -> bool:
        """Turn her replies in a group on or off; True when that changed anything."""
        with self._db() as db:
            if enabled:
                cursor = db.execute("DELETE FROM disabled_groups WHERE group_id=?", (group_id,))
            else:
                cursor = db.execute(
                    "INSERT OR IGNORE INTO disabled_groups (group_id) VALUES (?)", (group_id,)
                )
            return cursor.rowcount > 0

    # talk state, decision log and call fuse
    def talk(self, group_id: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM talk WHERE group_id=?", (group_id,))

    def save_talk(
        self, group_id: str, bias: float, her: float, total: float, heat: float, urge: float, now: float
    ) -> None:
        self._run(
            "INSERT INTO talk (group_id,bias,her,total,heat,urge,updated_at) VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(group_id) DO UPDATE SET bias=excluded.bias, her=excluded.her,"
            " total=excluded.total, heat=excluded.heat, urge=excluded.urge,"
            " updated_at=excluded.updated_at",
            (group_id, bias, her, total, heat, urge, now),
        )

    def log_talk(
        self, now: float, group_id: str, kind: str, p: float | None, outcome: str, features: str
    ) -> None:
        self._run(
            "INSERT INTO talk_log (at,group_id,kind,p,outcome,features) VALUES (?,?,?,?,?,?)",
            (now, group_id, kind, p, outcome, features),
        )

    def talk_log(self) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM talk_log ORDER BY at")

    def delete_talk_log_before(self, before: float) -> None:
        self._run("DELETE FROM talk_log WHERE at<?", (before,))

    def calls(self, group_id: str, day: str, hour: str) -> tuple[int, int]:
        """Model calls this group made today and in this hour."""
        row = self._one(
            "SELECT COALESCE(SUM(count),0) AS day_count,"
            " COALESCE(SUM(CASE WHEN hour=? THEN count ELSE 0 END),0) AS hour_count"
            " FROM model_calls WHERE group_id=? AND day=?",
            (hour, group_id, day),
        )
        return row["day_count"], row["hour_count"]

    def add_call(self, group_id: str, day: str, hour: str) -> None:
        """Count one call; earlier days are dropped."""
        with self._db() as db:
            db.execute("DELETE FROM model_calls WHERE day<?", (day,))
            db.execute(
                "INSERT INTO model_calls (group_id,day,hour,count) VALUES (?,?,?,1)"
                " ON CONFLICT(group_id,hour) DO UPDATE SET count=count+1",
                (group_id, day, hour),
            )

    # stickers
    def sticker(self, sticker_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM stickers WHERE id=?", (sticker_id,))

    def sticker_known(self, sha256: str) -> bool:
        return self._one("SELECT 1 FROM stickers WHERE sha256=?", (sha256,)) is not None

    def all_stickers(self) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM stickers ORDER BY id")

    def add_sticker(self, sha256: str, file: str, description: str, now: float) -> None:
        self._run(
            "INSERT INTO stickers (sha256,file,description,saved_at) VALUES (?,?,?,?)",
            (sha256, file, description, now),
        )

    def least_used_sticker(self) -> sqlite3.Row | None:
        return self._one("SELECT * FROM stickers ORDER BY used_count, saved_at LIMIT 1")

    def use_sticker(self, sticker_id: int, now: float) -> None:
        self._run(
            "UPDATE stickers SET used_count=used_count+1, last_used=? WHERE id=?",
            (now, sticker_id),
        )

    def delete_sticker(self, sticker_id: int) -> None:
        self._run("DELETE FROM stickers WHERE id=?", (sticker_id,))
