from __future__ import annotations

import dataclasses
import datetime
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from support import make_config, make_settings

from astrbot_plugin_innerlife import burst, diary, emotion, jobs, marker, memory  # noqa: E402
from astrbot_plugin_innerlife.mind import Mind  # noqa: E402
from astrbot_plugin_innerlife.settings import Settings, parse_clock  # noqa: E402
from astrbot_plugin_innerlife.store import Store  # noqa: E402

T = make_settings().temperament
HOUR = 3600.0

CONTRACT_COLUMNS = {
    "people": ["user_id", "name", "affinity", "impression", "last_interaction", "updated_at"],
    "memories": [
        "id", "user_id", "group_id", "content", "private", "status", "strength", "mentions",
        "created_at", "last_mentioned",
    ],
    "diaries": ["kind", "period", "body", "created_at", "edited_at"],
    "mood": ["id", "valence", "arousal", "energy", "updated_at"],
    "emotions": ["id", "kind", "target_user_id", "intensity", "cause", "created_at"],
    "life": ["day", "schedule", "weather", "day_kind", "updated_at"],
    "news": ["link", "title", "summary", "source", "published_at", "fetched_at", "used_at"],
    "chat_log": ["id", "group_id", "user_id", "name", "text", "at"],
    "night_jobs": ["day", "step"],
    "tool_usage": ["day", "name", "count"],
    "disabled_groups": ["group_id"],
    "talk": ["group_id", "bias", "her", "total", "heat", "urge", "updated_at"],
    "talk_log": ["at", "group_id", "kind", "p", "outcome", "features"],
    "model_calls": ["group_id", "day", "hour", "count"],
}


class EmotionTests(unittest.TestCase):
    def test_emotion_decays_and_grudges_last_longer(self) -> None:
        half = emotion.EMOTION_HALF_LIFE_MINUTES * 60
        self.assertAlmostEqual(emotion.decayed_intensity(1.0, half, T), 0.5)
        self.assertAlmostEqual(emotion.decayed_intensity(1.0, 0, T), 1.0)
        forgiving = dataclasses.replace(T, temper_grudge=0.0)
        grudging = dataclasses.replace(T, temper_grudge=1.0)
        self.assertLess(
            emotion.decayed_intensity(1.0, half, forgiving, grudge=True),
            emotion.decayed_intensity(1.0, half, grudging, grudge=True),
        )
        held = emotion.decayed_intensity(1.0, half, T, grudge=True, held=3.0)
        self.assertGreater(held, emotion.decayed_intensity(1.0, half, T, grudge=True))

    def test_mood_returns_to_the_baseline(self) -> None:
        valence, arousal = emotion.mood_now(-1.0, 1.0, 0, T)
        self.assertEqual((valence, arousal), (-1.0, 1.0))
        valence, arousal = emotion.mood_now(-1.0, 1.0, T.mood_half_life_hours * HOUR, T)
        self.assertAlmostEqual(valence, (T.mood_baseline_valence - 1.0) / 2)
        valence, _ = emotion.mood_now(-1.0, 1.0, 30 * 24 * HOUR, T)
        self.assertAlmostEqual(valence, T.mood_baseline_valence, places=3)

    def test_emotions_push_the_mood_within_bounds(self) -> None:
        valence, arousal = emotion.push_mood(0.2, 0.3, "生气", 1.0)
        self.assertLess(valence, 0.2)
        self.assertGreater(arousal, 0.3)
        for _ in range(50):
            valence, arousal = emotion.push_mood(valence, arousal, "生气", 1.0)
        self.assertGreaterEqual(valence, -1.0)
        self.assertLessEqual(arousal, 1.0)

    def test_affinity_fades_recovers_fast_and_has_a_floor(self) -> None:
        self.assertAlmostEqual(emotion.faded_affinity(40, T.affinity_fade_days * 86400, T), 20)
        self.assertAlmostEqual(emotion.faded_affinity(-40, 0, T), -40)
        hostile = emotion.apply_sentiment(0, -2, T)
        self.assertEqual(emotion.apply_sentiment(-21, 2, T), min(0.0, -21 + 2 * emotion.AFFINITY_STEP * T.temper_soften))
        self.assertLessEqual(emotion.apply_sentiment(-21, 2, T), 0)
        self.assertGreater(emotion.apply_sentiment(-30, 1, T) - -30, emotion.apply_sentiment(30, 1, T) - 30)
        value = 0.0
        for step in range(1, 4):
            value = emotion.apply_sentiment(value, 1, T)
            self.assertAlmostEqual(value, step * emotion.AFFINITY_STEP)
        self.assertLess(hostile, 0)
        value = 0.0
        for _ in range(100):
            value = emotion.apply_sentiment(value, -2, T)
        self.assertEqual(value, emotion.AFFINITY_FLOOR)
        for _ in range(100):
            value = emotion.apply_sentiment(value, 2, T)
        self.assertEqual(value, emotion.AFFINITY_MAX)

    def test_temperament_parameters_scale_the_effect(self) -> None:
        touchy = dataclasses.replace(T, temper_flare=2.0)
        self.assertLess(emotion.apply_sentiment(0, -1, touchy), emotion.apply_sentiment(0, -1, T))


class MarkerTests(unittest.TestCase):
    GOOD = (
        '<il>{"e":[{"k":"生气","i":0.9,"to":"甲","why":"骂我"}],'
        '"p":[{"n":"甲","s":-2,"imp":"嘴臭","m":[{"t":"在上班","x":0},{"t":"生病了","x":1}]}],'
        '"draw":"一条鲸鱼","news":[1,2,3]}</il>'
    )

    def test_marker_is_split_from_the_text(self) -> None:
        text, parsed = marker.parse_marker("哼 别惹我\n" + self.GOOD)
        self.assertEqual(text, "哼 别惹我")
        [note] = parsed.emotions
        self.assertEqual((note.kind, note.intensity, note.target), ("生气", 0.9, "甲"))
        [person] = parsed.people
        self.assertEqual((person.name, person.sentiment, person.impression), ("甲", -2.0, "嘴臭"))
        self.assertEqual([(m.text, m.private) for m in person.memories], [("在上班", False), ("生病了", True)])
        self.assertEqual(parsed.draw, "一条鲸鱼")
        self.assertEqual(parsed.news, (1, 2))

    def test_garbage_never_leaks_or_raises(self) -> None:
        cases = {
            "好的 <il>{bad json": "好的",
            "好的 <il>": "好的",
            "好的 <i": "好的",
            "好的 <il": "好的",
            "<il>[1,2]</il>好的": "好的",
            "好的</il>": "好的",
            "好的 <IL>{}</IL>": "好的",
            "好的 <il>```json\n{}\n```</il>": "好的",
            "好的": "好的",
            "": "",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                text, parsed = marker.parse_marker(raw)
                self.assertEqual(text, expected)
                self.assertNotIn("<il", text.lower())
                self.assertEqual(parsed, marker.Marker())

    def test_bad_fields_are_dropped_not_trusted(self) -> None:
        raw = (
            '<il>{"e":[{"k":"暴怒","i":5},{"k":"开心","i":9},"x",{"k":"开心","i":"高"}],'
            '"p":[{"n":"甲","s":99},{"n":"乙"},3],"news":["a",true,2]}</il>'
        )
        _, parsed = marker.parse_marker(raw)
        self.assertEqual([(e.kind, e.intensity) for e in parsed.emotions], [("开心", 1.0), ("开心", 0.5)])
        self.assertEqual([(p.name, p.sentiment) for p in parsed.people], [("甲", 2.0)])
        self.assertEqual(parsed.news, (2,))

    def test_rules_follow_the_enabled_features(self) -> None:
        self.assertEqual(marker.marker_rules(emotion=False, memory=False, draw=False), "")
        only_memory = marker.marker_rules(emotion=False, memory=True, draw=False)
        self.assertIn('"m"', only_memory)
        self.assertNotIn('"s"', only_memory)
        self.assertIn("draw：", marker.marker_rules(emotion=False, memory=False, draw=True))


class BurstTests(unittest.TestCase):
    def test_split_and_cap(self) -> None:
        self.assertEqual(burst.split_parts("甲<sp>乙 <SP/> 丙<sp>"), ["甲", "乙", "丙"])
        self.assertEqual(burst.plan_burst("甲<sp>乙<sp>丙", 3), ["甲", "乙", "丙"])
        self.assertEqual(burst.plan_burst("甲<sp>乙<sp>丙<sp>丁", 2), ["甲", "乙 丙 丁"])
        self.assertEqual(burst.plan_burst("甲<sp>乙", 1), ["甲 乙"])
        self.assertEqual(burst.plan_burst("没有分条", 4), ["没有分条"])

    def test_structured_or_long_text_stays_one_message(self) -> None:
        self.assertEqual(burst.plan_burst("步骤<sp>1. 先这样\n2. 再那样", 4), ["步骤 1. 先这样\n2. 再那样"])
        self.assertEqual(burst.plan_burst("看这个<sp>```code```", 4), ["看这个 ```code```"])
        self.assertEqual(len(burst.plan_burst("长" * 81 + "<sp>短", 4)), 1)
        self.assertEqual(len(burst.plan_burst("<sp>".join(["字" * 70] * 4), 4)), 1)

    def test_cap_follows_energy_and_arousal(self) -> None:
        self.assertEqual(burst.burst_cap(10, 0.3), 1)
        self.assertEqual(burst.burst_cap(50, 0.3), 2)
        self.assertEqual(burst.burst_cap(90, 0.3), 3)
        self.assertEqual(burst.burst_cap(90, 0.9), 4)
        self.assertEqual(burst.burst_rules(1), "")
        self.assertIn("2 到 3 条", burst.burst_rules(3))

    def test_typing_pause_is_bounded(self) -> None:
        self.assertEqual(burst.typing_delay("嗯", 0), burst.MIN_DELAY)
        self.assertEqual(burst.typing_delay("长" * 500, 1), burst.MAX_DELAY)


class MemoryTests(unittest.TestCase):
    def test_forgetting_curve(self) -> None:
        day = 86400.0
        self.assertAlmostEqual(memory.effective_strength(1.0, 1, 0.0, 30 * day, 30), 0.5)
        often = memory.effective_strength(1.0, 9, 0.0, 30 * day, 30)
        self.assertGreater(often, 0.5)
        self.assertEqual(memory.reinforced(2.5, 1.0, 1.0), memory.MAX_STRENGTH)
        self.assertAlmostEqual(memory.reinforced(1.0, 0.3, 1.0), 2.0)

    def test_same_memory_detection(self) -> None:
        self.assertTrue(memory.same_memory("在准备考研", "他在准备考研"))
        self.assertFalse(memory.same_memory("在准备考研", "喜欢吃火锅"))

    def test_parse_consolidation_is_tolerant(self) -> None:
        raw = (
            '```json\n{"memories":[{"id":3,"verdict":"fix","text":"改后","private":1},'
            '{"id":null,"user":"7","text":"新的"},{"id":"x","verdict":"keep"},'
            '{"id":4,"verdict":"maybe"},"junk"],"impressions":[{"user":"7","impression":"爱玩梗"}]}\n```'
        )
        result = memory.parse_consolidation(raw)
        self.assertEqual(
            [(m.id, m.verdict, m.text, m.private) for m in result.memories],
            [(3, "fix", "改后", True), (None, "keep", "新的", False)],
        )
        self.assertEqual(result.impressions, {"7": "爱玩梗"})
        for bad in ("", "没有json", "[]", '{"memories":3}'):
            self.assertIsNone(memory.parse_consolidation(bad))


class DiaryScheduleTests(unittest.TestCase):
    def test_night_jobs_by_date(self) -> None:
        monday, first = datetime.date(2026, 10, 19), datetime.date(2026, 11, 1)
        self.assertEqual(diary.jobs_for(datetime.date(2026, 10, 16)), ["daily"])
        self.assertEqual(diary.jobs_for(monday), ["daily", "weekly"])
        self.assertEqual(diary.jobs_for(first), ["daily", "monthly"])
        self.assertEqual(diary.jobs_for(datetime.date(2026, 6, 1)), ["daily", "weekly", "monthly"])

    def test_periods(self) -> None:
        self.assertEqual(
            diary.previous_iso_week(datetime.date(2026, 10, 19)),
            ("2026-W42", datetime.date(2026, 10, 12), datetime.date(2026, 10, 18)),
        )
        self.assertEqual(diary.previous_iso_week(datetime.date(2027, 1, 4))[0], "2026-W53")
        self.assertEqual(
            diary.previous_month(datetime.date(2026, 1, 1)),
            ("2025-12", datetime.date(2025, 12, 1), datetime.date(2025, 12, 31)),
        )

    def test_clock_setting(self) -> None:
        self.assertEqual(parse_clock("04:00"), datetime.time(4, 0))
        self.assertEqual(parse_clock("4:30"), datetime.time(4, 30))
        for bad in ("25:00", "4", "04:60", "abc"):
            with self.assertRaises(ValueError):
                parse_clock(bad)
        self.assertEqual(
            make_settings(consolidation_time="03:15").consolidation_time, datetime.time(3, 15)
        )

    def test_daily_template_is_forced(self) -> None:
        body = diary.normalize_daily("## 心情曲线\n早上困\n\n## 自己的想法\n还在生气\n## 乱写的标题\n忽略")
        headings = [line[3:] for line in body.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, list(diary.SECTIONS))
        self.assertEqual(diary.section(body, "心情曲线"), "早上困")
        self.assertEqual(diary.section(body, "做了什么正事"), "")
        self.assertNotIn("忽略", body)

    def test_recall_leaves_out_private_sections_and_is_bounded(self) -> None:
        body = diary.normalize_daily("## 心情曲线\n好\n## 记住的小事\n他分手了\n## 自己的想法\n想和甲和好")
        text = diary.recall_text([("2026-10-15", body)], ("2026-W41", "上周压缩"), None, 1000)
        self.assertIn("想和甲和好", text)
        self.assertNotIn("分手", text)
        self.assertIn("上周压缩", text)
        self.assertEqual(len(diary.recall_text([("2026-10-15", body)], None, None, 10)), 10)


class StoreTests(unittest.TestCase):
    def test_schema_matches_the_contract_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innerlife.db"
            Store(path)
            db = sqlite3.connect(path)
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            for table, columns in CONTRACT_COLUMNS.items():
                actual = [row[1] for row in db.execute(f"PRAGMA table_info({table})")]
                expected = columns + (["held"] if table == "emotions" else [])
                self.assertEqual(actual, expected, table)
            db.close()

    def test_old_emotions_table_gets_the_extra_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innerlife.db"
            db = sqlite3.connect(path)
            db.execute(
                "CREATE TABLE emotions (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, target_user_id TEXT,"
                " intensity REAL NOT NULL, cause TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL)"
            )
            db.commit()
            db.close()
            store = Store(path)
            store.add_emotion("开心", None, 0.5, "", 1.0)
            self.assertEqual(store.emotions_since(0)[0]["held"], 1.0)

    def test_round_trip_and_external_edits_are_seen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innerlife.db"
            store = Store(path)
            store.touch_person("1", "甲", 10.0)
            store.save_person("1", "甲", 12.5, "爱玩梗", 11.0)
            person = store.person("1")
            self.assertEqual((person["affinity"], person["impression"], person["last_interaction"]), (12.5, "爱玩梗", 11.0))
            memory_id = store.add_memory("1", "g", "在准备考研", True, 5.0)
            row = store.memories_of("1")[0]
            self.assertEqual((row["status"], row["strength"], row["mentions"], row["private"]), ("draft", 1, 1, 1))
            store.reinforce_memory(memory_id, 2.0, 9.0)
            row = store.memories_of("1")[0]
            self.assertEqual((row["strength"], row["mentions"], row["last_mentioned"]), (2.0, 2, 9.0))
            store.save_diary("daily", "2026-10-16", "正文", 1.0)
            store.save_mood(0.1, 0.2, 55.0, 3.0)
            self.assertEqual(store.mood()["energy"], 55.0)
            store.save_life("2026-10-16", 1.0, weather="晴")
            store.save_life("2026-10-16", 2.0, schedule="09:00 起床")
            life = store.life("2026-10-16")
            self.assertEqual((life["weather"], life["schedule"]), ("晴", "09:00 起床"))
            store.add_news([("l1", "标题", "摘要", "源", None), ("l1", "重复", "", "", None)], 1.0)
            self.assertEqual([n["title"] for n in store.unused_news(5)], ["标题"])
            store.mark_news_used(["l1"], 2.0)
            self.assertEqual(store.unused_news(5), [])
            store.log_line("g", "1", "甲", "你好", 4.0)
            self.assertEqual(len(store.chat_log()), 1)
            store.purge_chat_log(4.0)
            self.assertEqual(store.chat_log(), [])
            # An editor outside the bot changes a row; the next read sees it.
            outside = sqlite3.connect(path)
            outside.execute("UPDATE people SET impression='改过的' WHERE user_id='1'")
            outside.execute("UPDATE diaries SET body='手改', edited_at=9 WHERE kind='daily'")
            outside.commit()
            outside.close()
            self.assertEqual(store.person("1")["impression"], "改过的")
            self.assertEqual(store.diary("daily", "2026-10-16")["edited_at"], 9)

    def test_talk_state_and_fuse_counts_survive_a_new_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innerlife.db"
            store = Store(path)
            self.assertIsNone(store.talk("g"))
            store.save_talk("g", -2.5, 3.0, 40.0, 0.7, 0.4, 50.0)
            store.add_call("g", "2026-10-16", "2026-10-16 09")
            store.add_call("g", "2026-10-16", "2026-10-16 09")
            store.add_call("g", "2026-10-16", "2026-10-16 10")
            store.add_call("h", "2026-10-16", "2026-10-16 10")
            again = Store(path)
            row = again.talk("g")
            self.assertEqual(
                (row["bias"], row["her"], row["total"], row["heat"], row["urge"], row["updated_at"]),
                (-2.5, 3.0, 40.0, 0.7, 0.4, 50.0),
            )
            self.assertEqual(again.calls("g", "2026-10-16", "2026-10-16 09"), (3, 2))
            self.assertEqual(again.calls("g", "2026-10-16", "2026-10-16 11"), (3, 0))
            self.assertEqual(again.calls("g", "2026-10-17", "2026-10-17 00"), (0, 0))
            again.add_call("g", "2026-10-17", "2026-10-17 00")  # a new day drops the old counts
            self.assertEqual(again.calls("g", "2026-10-16", "2026-10-16 09"), (0, 0))

    def test_prune_keeps_the_talk_log_for_14_days(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "innerlife.db")
            day, now = 86400.0, 100 * 86400.0
            store.log_talk(now - 15 * day, "g", "chat", 0.2, "skip", "{}")
            store.log_talk(now - 13 * day, "g", "topic", 0.01, "sent", "{}")
            jobs.prune(store, make_settings(), datetime.date(2026, 10, 17), now)
            self.assertEqual([r["outcome"] for r in store.talk_log()], ["sent"])

    def test_prune_forgets_members_quiet_past_the_retention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "innerlife.db")
            day, now = 86400.0, 100 * 86400.0
            store.save_person("old", "老人", 10.0, "", now - 31 * day)
            store.add_memory("old", "g", "养了只猫", False, now - 31 * day)
            store.save_person("new", "新人", 10.0, "", now - 29 * day)
            today = datetime.date(2026, 10, 17)
            jobs.prune(store, make_settings(), today, now)
            self.assertIsNotNone(store.person("old"))
            jobs.prune(store, make_settings(person_keep_days=30), today, now)
            self.assertIsNone(store.person("old"))
            self.assertEqual(store.memories_of("old"), [])
            self.assertIsNotNone(store.person("new"))

    def test_night_progress_tool_usage_and_the_reply_switch_live_in_the_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innerlife.db"
            store = Store(path)
            self.assertEqual(store.night_steps("2026-10-16"), set())
            store.mark_night_step("2026-10-16", "daily")
            store.mark_night_step("2026-10-16", "daily")
            self.assertEqual(Store(path).night_steps("2026-10-16"), {"daily"})
            store.delete_night_before("2026-10-17")
            self.assertEqual(store.night_steps("2026-10-16"), set())

            self.assertEqual(store.tool_uses("2026-10-16", "draw"), 0)
            store.add_tool_use("2026-10-16", "draw")
            store.add_tool_use("2026-10-16", "draw")
            self.assertEqual(Store(path).tool_uses("2026-10-16", "draw"), 2)
            store.add_tool_use("2026-10-17", "draw")
            self.assertEqual(store.tool_uses("2026-10-16", "draw"), 0)
            self.assertEqual(store.tool_uses("2026-10-17", "draw"), 1)

            self.assertTrue(store.group_enabled("10001"))
            self.assertTrue(store.set_group_enabled("10001", False))
            self.assertFalse(store.set_group_enabled("10001", False))
            self.assertFalse(Store(path).group_enabled("10001"))
            self.assertTrue(store.group_enabled("10002"))
            self.assertTrue(store.set_group_enabled("10001", True))
            self.assertFalse(store.set_group_enabled("10001", True))
            self.assertTrue(Store(path).group_enabled("10001"))


class MindTests(unittest.TestCase):
    def test_marker_updates_affinity_impression_drafts_and_dedupes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "innerlife.db")
            mind = Mind(store, make_settings())
            _, parsed = marker.parse_marker(
                '<il>{"e":[{"k":"开心","i":0.8,"why":"被夸"}],'
                '"p":[{"s":2,"imp":"嘴甜","m":[{"t":"在准备考研"},{"t":"刚分手","x":1}]}]}</il>'
            )
            now = 1000.0
            mind.apply_marker(parsed, now, group_id="g", resolve=lambda _n: None, default=("1", "甲"))
            self.assertGreater(store.person("1")["affinity"], 0)
            self.assertEqual(store.person("1")["impression"], "嘴甜")
            self.assertEqual([(m["content"], m["private"]) for m in store.memories_of("1")], [("在准备考研", 0), ("刚分手", 1)])
            [felt] = mind.active_emotions(now)
            self.assertEqual(felt[0], "开心")
            self.assertGreater(mind.mood(now)[0], make_settings().temperament.mood_baseline_valence)
            mind.apply_marker(parsed, now + 10, group_id="g", resolve=lambda _n: None, default=("1", "甲"))
            self.assertEqual(len(store.memories_of("1")), 2)
            self.assertEqual(store.memories_of("1")[0]["mentions"], 2)

    def test_unknown_names_do_not_get_credit_in_a_group_judgement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "innerlife.db")
            mind = Mind(store, make_settings())
            _, parsed = marker.parse_marker('<il>{"p":[{"n":"路人","s":2,"m":[{"t":"x"}]}]}</il>')
            mind.apply_marker(parsed, 1.0, group_id="g", resolve=lambda _n: None)
            self.assertEqual(store.all_people(), [])
            self.assertEqual(store.all_memories(), [])

    def test_insult_drops_affinity_and_the_anger_lingers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "innerlife.db")
            mind = Mind(store, make_settings())
            mind.insulted("9", "九", 0.0)
            self.assertEqual(store.person("9")["affinity"], -12.0)
            later = 4 * HOUR
            [(kind, intensity, target, _)] = mind.active_emotions(later)
            self.assertEqual((kind, target), ("生气", "9"))
            ordinary = emotion.decayed_intensity(0.8, later, T, grudge=True)
            self.assertGreater(intensity, ordinary)

    def test_temper_flare_only_applies_to_emotions_aimed_at_someone(self) -> None:
        touchy = dataclasses.replace(make_settings(), temperament=dataclasses.replace(T, temper_flare=2.0))
        with tempfile.TemporaryDirectory() as tmp:
            mind = Mind(Store(Path(tmp) / "innerlife.db"), touchy)
            mind.feel("无聊", 0.4, None, "没事干", 0.0)
            mind.feel("烦躁", 0.4, "9", "被抬杠", 0.0)
            felt = {kind: intensity for kind, intensity, _, _ in mind.active_emotions(0.0)}
            self.assertAlmostEqual(felt["无聊"], 0.4)
            self.assertAlmostEqual(felt["烦躁"], 0.8)

    def test_state_text_hides_private_memories_and_offers_venting_to_friends(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "innerlife.db")
            mind = Mind(store, make_settings())
            now = time.time()
            store.save_person("1", "甲", 50.0, "很会聊天", now)
            store.save_person("2", "乙", -30.0, "总抬杠", now)
            store.add_memory("1", "g", "在准备考研", False, now)
            store.add_memory("1", "g", "刚分手", True, now)
            mind.insulted("2", "乙", now)
            text = mind.mood_text(now, [("1", "甲"), ("2", "乙")])
            self.assertIn("生气对乙", text)
            self.assertIn("吐吐苦水", text)
            self.assertIn("甲", text.split("吐吐苦水")[0].split("在场的")[-1])
            people = mind.people_text([("1", "甲"), ("2", "乙")], now)
            self.assertIn("在准备考研", people)
            self.assertNotIn("分手", people)
            self.assertIn("总抬杠", people)

    def test_forgotten_memories_leave_the_prompt_and_the_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "innerlife.db")
            settings = make_settings()
            mind = Mind(store, settings)
            old = time.time() - 400 * 86400
            store.add_memory("1", "g", "很久以前的事", False, old)
            store.save_person("1", "甲", 0.0, "有印象", time.time())
            self.assertNotIn("很久以前", mind.people_text([("1", "甲")], time.time()))
            self.assertEqual(jobs.forget(store, settings, time.time()), 0)
            store.update_memory(1, "很久以前的事", False, "kept")
            self.assertEqual(jobs.forget(store, settings, time.time()), 1)
            self.assertEqual(store.all_memories(), [])


class NightJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_night_run_consolidates_writes_diaries_and_purges(self) -> None:
        monday = datetime.date(2026, 10, 19)
        settings = make_settings()
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "innerlife.db")
            now = time.time()
            store.touch_person("1", "甲", now)
            store.log_line("g", "1", "甲", "我在准备考研", now - 100)
            store.log_line("g", "self", "你", "加油", now - 90)
            keep = store.add_memory("1", "g", "在准备考研", False, now)
            drop = store.add_memory("1", "g", "说她是猫", False, now)
            calls: list[str] = []

            async def call(system: str, prompt: str):
                calls.append(system[:8])
                if system == memory.CONSOLIDATION_PROMPT:
                    return (
                        f'{{"memories":[{{"id":{keep},"verdict":"keep","private":1}},'
                        f'{{"id":{drop},"verdict":"drop"}},'
                        '{"id":null,"user":"1","text":"想考研","private":0},'
                        '{"id":null,"user":"99","text":"陌生人","private":0}],'
                        '"impressions":[{"user":"1","impression":"上进"}]}'
                    )
                if system == diary.DAILY_PROMPT:
                    return "## 心情曲线\n还行\n## 明天的打算\n睡觉"
                return "压缩的一周"

            store.add_emotion("开心", "1", 0.5, "被夸", now - 50)
            self.assertTrue(await jobs.run_night(store, call, settings, monday, now))
            self.assertEqual(
                store.night_steps("2026-10-19"), {"consolidate", "daily", "weekly", "complete"}
            )
            self.assertEqual(
                sorted((m["content"], m["status"]) for m in store.all_memories()),
                [("在准备考研", "kept"), ("想考研", "kept")],
            )
            self.assertEqual(
                {m["content"]: m["private"] for m in store.all_memories()},
                {"在准备考研": 1, "想考研": 0},
            )
            self.assertEqual(store.person("1")["impression"], "上进")
            daily = store.diary("daily", "2026-10-18")
            self.assertEqual(diary.section(daily["body"], "明天的打算"), "睡觉")
            self.assertIsNotNone(store.diary("weekly", "2026-W42"))
            self.assertIsNone(store.diary("monthly", "2026-09"))
            self.assertEqual(store.chat_log(), [])
            self.assertEqual(len(calls), 3)
            self.assertTrue(await jobs.run_night(store, call, settings, monday, now))
            self.assertEqual(len(calls), 3)

    async def test_a_long_day_is_read_in_chunks(self) -> None:
        today = datetime.date(2026, 10, 16)
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "innerlife.db")
            now = time.time()
            for index in range(6):
                store.log_line("g", "1", "甲", f"第{index}件事" + "字" * 30, now - 100 + index)
            draft = store.add_memory("1", "g", "草稿", False, now)
            seen: dict[str, list[str]] = {"consolidate": [], "summary": [], "daily": []}

            async def call(system: str, prompt: str):
                if system == memory.CONSOLIDATION_PROMPT:
                    seen["consolidate"].append(prompt)
                    n = len(seen["consolidate"])
                    return f'{{"memories":[{{"id":null,"user":"1","text":"记{n}","private":0}}]}}'
                if system == diary.CHUNK_SUMMARY_PROMPT:
                    seen["summary"].append(prompt)
                    return f"摘要{len(seen['summary'])}"
                seen["daily"].append(prompt)
                return "## 心情曲线\n好"

            with mock.patch.object(jobs, "CONSOLIDATION_MAX_CHARS", 100):
                self.assertTrue(await jobs.run_night(store, call, make_settings(), today, now))
            self.assertGreater(len(seen["consolidate"]), 1)
            self.assertEqual(len(seen["summary"]), len(seen["consolidate"]))
            for index in range(6):
                self.assertEqual(sum(f"第{index}件事" in p for p in seen["consolidate"]), 1)
                self.assertEqual(sum(f"第{index}件事" in p for p in seen["summary"]), 1)
            self.assertTrue(all("第0件事" not in p for p in seen["daily"]))
            self.assertIn("摘要1", seen["daily"][0])
            self.assertEqual(store.chat_log(), [])
            self.assertEqual(
                sorted(m["content"] for m in store.all_memories() if m["id"] != draft),
                sorted(f"记{n}" for n in range(1, len(seen["consolidate"]) + 1)),
            )

    async def test_failed_step_keeps_the_log_and_retries_only_that_step(self) -> None:
        today = datetime.date(2026, 10, 16)
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "innerlife.db")
            now = time.time()
            store.log_line("g", "1", "甲", "你好", now - 10)
            store.add_memory("1", "g", "草稿", False, now)
            answers = {"consolidate": None}

            async def call(system: str, prompt: str):
                if system == memory.CONSOLIDATION_PROMPT:
                    return answers["consolidate"]
                return "## 心情曲线\n好"

            self.assertFalse(await jobs.run_night(store, call, make_settings(), today, now))
            self.assertEqual(store.night_steps("2026-10-16"), {"daily"})
            self.assertEqual(len(store.chat_log()), 1)
            self.assertEqual(store.drafts()[0]["content"], "草稿")
            answers["consolidate"] = '{"memories":[{"id":1,"verdict":"keep"}]}'
            self.assertTrue(await jobs.run_night(store, call, make_settings(), today, now))
            self.assertEqual(store.chat_log(), [])
            self.assertEqual(store.drafts(), [])

    async def test_a_failure_after_consolidation_never_repeats_it(self) -> None:
        today = datetime.date(2026, 10, 16)
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "innerlife.db")
            now = time.time()
            store.log_line("g", "1", "甲", "我养了只猫", now - 10)
            calls: list[str] = []
            diary_works = {"yes": False}

            async def call(system: str, prompt: str):
                calls.append(system)
                if system == memory.CONSOLIDATION_PROMPT:
                    return '{"memories":[{"id":null,"user":"1","text":"养了只猫","private":0}]}'
                return "## 心情曲线\n好" if diary_works["yes"] else None

            settings = make_settings()
            self.assertFalse(await jobs.run_night(store, call, settings, today, now))
            self.assertFalse(await jobs.run_night(store, call, settings, today, now))
            self.assertEqual(calls.count(memory.CONSOLIDATION_PROMPT), 1)
            diary_works["yes"] = True
            self.assertTrue(await jobs.run_night(store, call, settings, today, now))
            self.assertEqual(calls.count(memory.CONSOLIDATION_PROMPT), 1)
            self.assertEqual([m["content"] for m in store.all_memories()], ["养了只猫"])
            self.assertEqual(store.chat_log(), [])

    async def test_switched_off_features_skip_their_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "innerlife.db")
            now = time.time()
            store.log_line("g", "1", "甲", "你好", now - 10)

            async def call(system: str, prompt: str):
                raise AssertionError("no model call expected")

            settings = make_settings(memory__enabled=False, diary=False)
            self.assertTrue(
                await jobs.run_night(store, call, settings, datetime.date(2026, 10, 16), now)
            )
            self.assertEqual(store.chat_log(), [])

    async def test_upkeep_runs_the_night_once_at_its_time(self) -> None:
        tz = make_settings().tz
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "innerlife.db")
            store.log_line("g", "1", "甲", "你好", time.time() - 60)
            calls: list[str] = []

            async def call(system: str, prompt: str):
                calls.append(system)
                return "## 心情曲线\n好"

            clock = {"now": datetime.datetime(2026, 10, 16, 3, 59, tzinfo=tz)}
            upkeep = jobs.Upkeep(store, call, make_settings(memory__enabled=False), None, lambda: clock["now"])
            await upkeep.night()
            self.assertEqual(calls, [])
            clock["now"] = datetime.datetime(2026, 10, 16, 4, 0, tzinfo=tz)
            await upkeep.night()
            self.assertEqual(len(calls), 1)
            await upkeep.night()
            self.assertEqual(len(calls), 1)
            self.assertEqual(store.chat_log(), [])

    async def test_schedule_uses_the_day_weather_and_yesterdays_plan(self) -> None:
        tz = make_settings().tz
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "innerlife.db")
            store.save_life("2026-10-16", 1.0, weather="多云 29°C", day_kind="工作日")
            store.save_life("2026-10-15", 1.0, schedule="09:00 赖床\n01:30 睡觉")
            store.save_diary("daily", "2026-10-15", diary.normalize_daily("## 明天的打算\n去海边"), 1.0)
            seen: list[tuple[str, str]] = []

            async def call(system: str, prompt: str):
                seen.append((system, prompt))
                return '09:00 起床\n12:00 吃饭\n<il>{"e":[{"k":"困","i":0.5,"why":"没睡醒"}]}</il>'

            settings = make_settings(city_name="三亚", persona_prompt="人设")
            clock = [datetime.datetime(2026, 10, 16, 8, 0, tzinfo=tz)]
            upkeep = jobs.Upkeep(store, call, settings, None, lambda: clock[0])
            self.assertIsNone(await upkeep.schedule())  # still asleep until 08:30
            self.assertEqual(seen, [])
            clock[0] = datetime.datetime(2026, 10, 16, 8, 30, tzinfo=tz)
            parsed = await upkeep.schedule()
            self.assertEqual(store.life("2026-10-16")["schedule"], "09:00 起床\n12:00 吃饭")
            self.assertEqual(store.life("2026-10-16")["weather"], "多云 29°C")
            self.assertEqual([e.kind for e in parsed.emotions], ["困"])
            system, prompt = seen[0]
            self.assertIn("住在三亚", system)
            for kind in emotion.KINDS:
                self.assertIn(kind, system)
            for wanted in ("多云 29°C", "工作日", "09:00 赖床", "去海边", "周五", "醒来时间：08:30"):
                self.assertIn(wanted, prompt)
            self.assertIsNone(await upkeep.schedule())
            self.assertEqual(len(seen), 1)

    def test_sleep_window_follows_the_bedtime_line(self) -> None:
        tz = make_settings().tz
        day = datetime.date(2026, 10, 16)
        at = lambda d, h, m: datetime.datetime(2026, 10, d, h, m, tzinfo=tz)  # noqa: E731
        cases = {
            "09:00 起床\n23:40 洗漱睡觉": (at(16, 23, 40), at(17, 6, 40)),
            "09:00 起床\n02:10 睡觉": (at(17, 2, 10), at(17, 9, 10)),
            "07:00 睡醒\n12:00 午睡": (at(17, 0, 0), at(17, 7, 0)),
            "": (at(17, 0, 0), at(17, 7, 0)),
        }
        for schedule, window in cases.items():
            self.assertEqual(jobs.sleep_window(schedule, day, tz), window, schedule)

    async def test_log_chunks_use_the_given_timezone_and_cover_every_line(self) -> None:
        row = {"at": 0.0, "name": "甲", "user_id": "1", "text": "嗨"}
        utc = datetime.timezone.utc
        plus8 = datetime.timezone(datetime.timedelta(hours=8))
        self.assertEqual(jobs.log_chunks([row], 100, utc), ["00:00 甲(1)：嗨"])
        self.assertEqual(jobs.log_chunks([row], 100, plus8), ["08:00 甲(1)：嗨"])
        self.assertEqual(jobs.log_chunks([], 100, utc), [""])
        rows = [{**row, "text": str(n) * 10} for n in range(5)]  # each line is 21 characters
        chunks = jobs.log_chunks(rows, 50, utc)
        self.assertEqual(len(chunks), 3)
        self.assertTrue(all(len(chunk) <= 50 for chunk in chunks))
        self.assertEqual("\n".join(chunks), "\n".join(jobs.log_chunks(rows, 10**6, utc)))


class SettingsTests(unittest.TestCase):
    def test_schema_defaults_build_the_settings(self) -> None:
        s = make_settings()
        self.assertEqual((s.vision, s.search_rules, s.person_keep_days), (True, False, 180))
        self.assertEqual((s.daily_call_fuse, s.talk_share, s.talk_temperature), (300, 0.10, 1.0))
        self.assertEqual((s.talk_stamina_minutes, s.talk_urge_hours, s.talk_contagion), (45, 4, 0.5))
        self.assertEqual(s.draw_tool_arguments, {})
        self.assertIn("习近平", s.draw_blocked_terms)
        self.assertTrue(s.news_feeds and s.news_blocked_terms)

    def test_bad_values_are_rejected(self) -> None:
        for overrides in (
            {"group_ids": ["abc"]},
            {"switch_command": " "},
            {"astrbot_persona_id": ""},
            {"consolidation_time": "25:00"},
            {"temperature": 0},
            {"stamina_minutes": 0},
            {"urge_hours": -1},
        ):
            with self.assertRaises(ValueError, msg=overrides):
                make_settings(**overrides)

    def test_every_schema_key_is_read(self) -> None:
        # Each key changes the settings, so none is dead.
        base = make_settings()
        probes = {"int": 7, "float": 0.25, "string": "x", "list": ["1"]}
        schema = make_config()
        for group, keys in schema.items():
            for key, value in keys.items():
                if key in ("consolidation_time", "draw_tool_arguments", "timezone", "group_ids"):
                    continue
                kind = (
                    "bool" if isinstance(value, bool) else "int" if isinstance(value, int)
                    else "float" if isinstance(value, float) else "list" if isinstance(value, list)
                    else "string"
                )
                probe = not value if kind == "bool" else probes[kind]
                changed = make_config(**{f"{group}__{key}": probe})
                self.assertNotEqual(Settings.from_config(changed), base, f"{group}.{key} unused")


if __name__ == "__main__":
    unittest.main()
