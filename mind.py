from __future__ import annotations

import datetime
from collections.abc import Callable

from . import diary, emotion
from .emotion import KINDS
from .life import WEEKDAYS
from .marker import Marker
from .memory import (
    FORGET_THRESHOLD,
    MEMORY_REINFORCE,
    effective_strength,
    reinforced,
    same_memory,
)
from .settings import Settings
from .store import Store

MAX_EMOTIONS_IN_PROMPT = 4
MIN_FELT = 0.1
NEGATIVE = -0.2
INSULT_PENALTY = 12.0  # affinity lost when someone insults her to her face; scaled by temper_flare
PROMPT_PEOPLE = 4  # people whose memories go into one reply's prompt
PROMPT_PER_PERSON = 5  # memories per person
DIARY_RECALL_DAYS = 3  # recent days of diary she reads in full; older ones come as weekly/monthly summaries
DIARY_PROMPT_CHARS = 1200

# resolve(name) -> (user_id, name) of a person who spoke in this conversation, else None
Resolve = Callable[[str], tuple[str, str] | None]


class Mind:
    """Emotions, affinity and memories on top of the store; every read goes to the db."""

    def __init__(self, store: Store, settings: Settings) -> None:
        self.store = store
        self.s = settings

    # background mood and energy
    def mood(self, now: float) -> tuple[float, float]:
        t = self.s.temperament
        row = self.store.mood()
        if row is None:
            return t.mood_baseline_valence, emotion.MOOD_BASELINE_AROUSAL
        return emotion.mood_now(row["valence"], row["arousal"], now - row["updated_at"], t)

    def saved_energy(self) -> float | None:
        row = self.store.mood()
        return None if row is None else row["energy"]

    def save_energy(self, energy: float, now: float) -> None:
        valence, arousal = self.mood(now)
        self.store.save_mood(valence, arousal, energy, now)

    def _push(self, kind: str, intensity: float, now: float) -> None:
        valence, arousal = emotion.push_mood(*self.mood(now), kind, intensity)
        row = self.store.mood()
        self.store.save_mood(valence, arousal, row["energy"] if row else 60.0, now)

    # emotions
    def feel(
        self,
        kind: str,
        intensity: float,
        target: str | None,
        cause: str,
        now: float,
        held: float = 1.0,
    ) -> None:
        if KINDS[kind][0] < 0 and target:
            intensity = min(1.0, intensity * self.s.temperament.temper_flare)
        self.store.add_emotion(kind, target, intensity, cause, now, held)
        self._push(kind, intensity, now)

    def active_emotions(self, now: float) -> list[tuple[str, float, str | None, str]]:
        """(kind, current intensity, target user id, cause), strongest first."""
        t = self.s.temperament
        found = []
        for row in self.store.emotions_since(now - 3 * 86400):
            current = emotion.decayed_intensity(
                row["intensity"],
                now - row["created_at"],
                t,
                grudge=KINDS[row["kind"]][0] < 0 and row["target_user_id"] is not None,
                held=row["held"],
            )
            if current >= MIN_FELT:
                found.append((row["kind"], current, row["target_user_id"], row["cause"]))
        return sorted(found, key=lambda item: -item[1])

    # people
    def affinity(self, user_id: str, now: float) -> float:
        row = self.store.person(user_id)
        if row is None:
            return 0.0
        idle = now - row["last_interaction"] if row["last_interaction"] else 0.0
        return emotion.faded_affinity(row["affinity"], idle, self.s.temperament)

    def _treat(self, user_id: str, name: str, sentiment: float, impression: str, now: float) -> None:
        row = self.store.person(user_id)
        affinity = emotion.apply_sentiment(
            self.affinity(user_id, now), sentiment, self.s.temperament
        )
        self.store.save_person(
            user_id, name, affinity, impression or (row["impression"] if row else ""), now
        )

    def insulted(self, user_id: str, name: str, now: float) -> None:
        """She was insulted to her face: affinity drops and the anger is held longer."""
        t = self.s.temperament
        row = self.store.person(user_id)
        affinity = max(
            emotion.AFFINITY_FLOOR,
            self.affinity(user_id, now) - INSULT_PENALTY * t.temper_flare,
        )
        self.store.save_person(user_id, name, affinity, row["impression"] if row else "", now)
        self.feel("生气", 0.8, user_id, "被骂了", now, emotion.INSULT_GRUDGE_MULTIPLIER)

    # memories
    def remember(self, user_id: str, group_id: str, text: str, private: bool, now: float) -> None:
        """Write a live draft, or reinforce the memory it repeats."""
        for row in self.store.memories_of(user_id):
            if same_memory(row["content"], text):
                current = effective_strength(
                    row["strength"], row["mentions"], row["last_mentioned"], now,
                    self.s.memory_half_life_days,
                )
                self.store.reinforce_memory(
                    row["id"], reinforced(row["strength"], current, MEMORY_REINFORCE), now
                )
                return
        self.store.add_memory(user_id, group_id, text, private, now)

    def apply_marker(
        self,
        marker: Marker,
        now: float,
        *,
        group_id: str,
        resolve: Resolve,
        default: tuple[str, str] | None = None,
    ) -> None:
        """Turn the hidden marker of one model answer into emotions, affinity, impressions and drafts."""
        if self.s.emotion:
            for note in marker.emotions:
                target = resolve(note.target) if note.target else None
                self.feel(note.kind, note.intensity, target[0] if target else None, note.cause, now)
        for note in marker.people:
            who = (resolve(note.name) if note.name else None) or default
            if who is None:
                continue
            user_id, name = who
            if self.s.emotion and (note.sentiment or note.impression):
                self._treat(user_id, name, note.sentiment, note.impression, now)
            if self.s.memory:
                for memory in note.memories:
                    self.remember(user_id, group_id, memory.text, memory.private, now)

    # prompt text
    def _strength_words(self, intensity: float) -> str:
        return "强烈" if intensity >= 0.7 else "明显" if intensity >= 0.4 else "一点点"

    def mood_text(self, now: float, people: list[tuple[str, str]]) -> str:
        names = {user_id: name for user_id, name in people}
        valence, arousal = self.mood(now)
        lines = [f"你现在{emotion.mood_words(valence, arousal)}。"]
        felt = self.active_emotions(now)[:MAX_EMOTIONS_IN_PROMPT]
        if felt:
            parts = []
            for kind, intensity, target, cause in felt:
                who = ""
                if target:
                    row = self.store.person(target)
                    who = f"对{names.get(target) or (row['name'] if row else '某人')}"
                parts.append(
                    f"{self._strength_words(intensity)}{kind}{who}" + (f"（{cause}）" if cause else "")
                )
            lines.append("此刻的情绪：" + "；".join(parts) + "。")
        lines.append(
            "你可以自然地把心情说出来（比如“别惹我 今天被气死了”），被问起时讲讲原因。"
            "但情绪只改变口气：回答的内容、准确度和完整度不变，答疑照样给全。"
        )
        angry_at = {target for kind, intensity, target, _ in felt if KINDS[kind][0] < 0 and target}
        upset = valence <= NEGATIVE or any(
            KINDS[kind][0] < 0 and intensity >= 0.5 for kind, intensity, _, _ in felt
        )
        friends = [
            name
            for user_id, name in people
            if user_id not in angry_at and self.affinity(user_id, now) >= emotion.AFFINITY_LIKE
        ]
        if upset and friends:
            lines.append(
                f"你心情不好，在场的{'、'.join(friends)}和你关系不错，可以对他们吐吐苦水；不要对惹你的人吐。"
            )
        return "\n".join(lines)

    def people_text(self, people: list[tuple[str, str]], now: float) -> str:
        lines = []
        for user_id, name in people[:PROMPT_PEOPLE]:
            row = self.store.person(user_id)
            affinity = self.affinity(user_id, now)
            memories = sorted(
                (
                    (
                        effective_strength(
                            m["strength"], m["mentions"], m["last_mentioned"], now,
                            self.s.memory_half_life_days,
                        ),
                        m["content"],
                    )
                    for m in self.store.memories_of(user_id, private=False)
                ),
                reverse=True,
            )
            kept = [
                text
                for strength, text in memories[:PROMPT_PER_PERSON]
                if strength > FORGET_THRESHOLD
            ] if self.s.memory else []
            impression = row["impression"] if row else ""
            if not kept and not impression and abs(affinity) < 15:
                continue
            line = f"- {name}：{emotion.affinity_words(affinity)}"
            if impression:
                line += f"，印象：{impression}"
            if kept:
                line += "；记得：" + "；".join(kept)
            lines.append(line)
        if not lines:
            return ""
        return (
            "你对在场的人：\n" + "\n".join(lines)
            + "\n记忆要像真人那样自然提起，不要背诵；没把握的细节不要说。"
            "人对你的态度也会影响你对他的口气：喜欢的人软一点，反感的人冷一点。"
        )

    def life_text(self, day: datetime.datetime) -> str:
        row = self.store.life(str(day.date()))
        head = f"现在是{day.year}年{day.month}月{day.day}日 {WEEKDAYS[day.weekday()]} {day:%H:%M}。"
        if row is None:
            return head
        lines = [head]
        if row["day_kind"]:
            lines.append(f"今天是{row['day_kind']}。")
        if row["weather"]:
            lines.append(f"天气：{row['weather']}。")
        if row["schedule"]:
            lines.append("你今天的日程（按这个过日子，聊到时自然带出，别念稿）：\n" + row["schedule"])
        return "\n".join(lines)

    def diary_text(self, today: datetime.date) -> str:
        if not self.s.diary:
            return ""
        dailies = [
            (row["period"], row["body"])
            for row in self.store.recent_diaries("daily", DIARY_RECALL_DAYS + 1)
            if row["period"] < str(today)
        ][:DIARY_RECALL_DAYS]
        weekly = self.store.recent_diaries("weekly", 1)
        monthly = self.store.recent_diaries("monthly", 1)
        text = diary.recall_text(
            dailies,
            (weekly[0]["period"], weekly[0]["body"]) if weekly else None,
            (monthly[0]["period"], monthly[0]["body"]) if monthly else None,
            DIARY_PROMPT_CHARS,
        )
        if not text:
            return ""
        return (
            "你的日记摘要（你自己记得的近况，越久远越模糊；其中的“自己的想法”可以影响你今天的态度，"
            "比如还在生谁的气，或想找谁和好）：\n" + text
        )
