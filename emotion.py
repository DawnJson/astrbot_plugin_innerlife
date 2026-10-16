from __future__ import annotations

from dataclasses import dataclass

# kind -> (valence -1..1, arousal 0..1)
KINDS: dict[str, tuple[float, float]] = {
    "开心": (0.8, 0.6),
    "兴奋": (0.8, 0.9),
    "得意": (0.6, 0.6),
    "感动": (0.7, 0.4),
    "害羞": (0.3, 0.6),
    "好奇": (0.3, 0.5),
    "平静": (0.0, 0.2),
    "困": (-0.1, 0.0),
    "无聊": (-0.2, 0.1),
    "无语": (-0.3, 0.3),
    "担心": (-0.3, 0.5),
    "烦躁": (-0.5, 0.7),
    "委屈": (-0.6, 0.4),
    "失落": (-0.6, 0.2),
    "生气": (-0.7, 0.9),
}
MOOD_PUSH = 0.4
INSULT_GRUDGE_MULTIPLIER = 3.0
AFFINITY_MAX = 100.0
AFFINITY_FLOOR = -60.0
AFFINITY_STEP = 4.0  # plain affinity change per sentiment point; temper_flare/temper_soften scale it
AFFINITY_LIKE = 25.0  # "有好感"; also how fond she must be of someone to vent to them
EMOTION_HALF_LIFE_MINUTES = 90.0
MOOD_BASELINE_AROUSAL = 0.3


@dataclass(frozen=True)
class Temperament:
    """Her temperament; the settings pick it (the schema default is a tsundere: flares fast, forgives fast, softens at kindness)."""

    temper_flare: float  # multiplies how hard hostile treatment lands
    temper_grudge: float  # 0 forgets at once, 1 holds on for long
    temper_soften: float  # multiplies the plain step when kindness wins back negative affinity (up to 0)
    mood_half_life_hours: float
    mood_baseline_valence: float
    affinity_fade_days: float


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def decayed_intensity(
    intensity: float, age_seconds: float, t: Temperament, *, grudge: bool = False, held: float = 1.0
) -> float:
    """Current strength of an emotion; anger/hurt at a person lasts with temper_grudge, `held` stretches it further."""
    half_life = EMOTION_HALF_LIFE_MINUTES * 60 * held
    if grudge:
        half_life *= 0.5 + 1.5 * t.temper_grudge
    return intensity * 0.5 ** (max(0.0, age_seconds) / half_life)


def mood_now(valence: float, arousal: float, age_seconds: float, t: Temperament) -> tuple[float, float]:
    """Background mood drifts back to the baseline."""
    keep = 0.5 ** (max(0.0, age_seconds) / (t.mood_half_life_hours * 3600))
    return (
        t.mood_baseline_valence + (valence - t.mood_baseline_valence) * keep,
        MOOD_BASELINE_AROUSAL + (arousal - MOOD_BASELINE_AROUSAL) * keep,
    )


def push_mood(valence: float, arousal: float, kind: str, intensity: float) -> tuple[float, float]:
    kind_valence, kind_arousal = KINDS[kind]
    weight = MOOD_PUSH * intensity
    return (
        _clamp(valence + (kind_valence - valence) * weight, -1.0, 1.0),
        _clamp(arousal + (kind_arousal - arousal) * weight, 0.0, 1.0),
    )


def faded_affinity(affinity: float, idle_seconds: float, t: Temperament) -> float:
    """Feelings about someone fade toward neutral while nothing happens."""
    return affinity * 0.5 ** (max(0.0, idle_seconds) / (t.affinity_fade_days * 86400))


def apply_sentiment(affinity: float, sentiment: float, t: Temperament) -> float:
    """Move affinity by how a person treated her (-2..2). Kindness wins back lost ground
    `temper_soften` times faster than a plain step, but only up to neutral; above neutral it
    grows at the plain step."""
    sentiment = _clamp(sentiment, -2.0, 2.0)
    if sentiment < 0:
        return _clamp(affinity + sentiment * AFFINITY_STEP * t.temper_flare, AFFINITY_FLOOR, AFFINITY_MAX)
    if affinity < 0:
        return min(0.0, affinity + sentiment * AFFINITY_STEP * t.temper_soften)
    return _clamp(affinity + sentiment * AFFINITY_STEP, AFFINITY_FLOOR, AFFINITY_MAX)


def mood_words(valence: float, arousal: float) -> str:
    if valence >= 0.5:
        base = "心情很好"
    elif valence >= 0.15:
        base = "心情不错"
    elif valence > -0.15:
        base = "心情一般"
    elif valence > -0.5:
        base = "心情有点差"
    else:
        base = "心情很差"
    if arousal >= 0.7:
        return base + "，情绪很激动"
    if arousal <= 0.15:
        return base + "，提不起劲"
    return base


def affinity_words(affinity: float) -> str:
    if affinity >= 60:
        return "很喜欢"
    if affinity >= AFFINITY_LIKE:
        return "有好感"
    if affinity > -15:
        return "一般"
    if affinity > -40:
        return "有点反感"
    return "很反感"
