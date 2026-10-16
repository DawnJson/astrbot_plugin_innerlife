"""How much she talks: a per-group state turned into a probability for each decision.

Pure functions and one dataclass, no AstrBot and no I/O; main.py loads and saves the state
through the store and rolls the dice. Formulas and knobs are described in docs/design.md."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

HOURLY_CALL_FUSE = 30  # background chat model calls per group per hour
TALK_LOG_KEEP_DAYS = 14

BIAS_START = -3.0
BIAS_RANGE = (-6.0, -1.0)
BIAS_RATE = 0.05
COUNT_HALF_LIFE_SECONDS = 86400.0
MIN_TOTAL_FOR_SHARE = 20.0
HEAT_DECAY_SECONDS = 240.0
MIN_BREADTH = 0.05  # floor of energy / 100 so a tired moment never zeroes the odds

MENTION_WEIGHT = 2.0
REPLY_WEIGHT = 2.2
QUESTION_WEIGHT = 0.8
AFFINITY_WEIGHT = 1.0
AFFINITY_RANGE = (-0.6, 1.0)
AROUSAL_WEIGHT = 0.8
COUNT_WEIGHT = 0.5

TOPIC_RATE = 0.0127  # per minute at full urge and full energy
TOPIC_SENT_URGE = 0.3
TOPIC_MISS_URGE = 0.6


@dataclass
class TalkState:
    """One group's talking state; `her` and `total` are decayed line counts."""

    bias: float = BIAS_START
    her: float = 0.0
    total: float = 0.0
    heat: float = 0.0
    urge: float = 0.0
    updated_at: float = 0.0

    def advance(self, now: float, *, awake: bool, urge_hours: float) -> None:
        """Bring the state to `now`: counts and heat decay, the urge grows while she is awake."""
        dt = max(0.0, now - self.updated_at)
        self.her *= 0.5 ** (dt / COUNT_HALF_LIFE_SECONDS)
        self.total *= 0.5 ** (dt / COUNT_HALF_LIFE_SECONDS)
        self.heat *= math.exp(-dt / HEAT_DECAY_SECONDS)
        if awake:
            self.urge += (1 - self.urge) * (1 - math.exp(-dt / (urge_hours * 3600)))
        self.updated_at = now

    def share_now(self, target: float) -> float:
        """Her share of the lines lately; with too little data it counts as on target."""
        return self.her / self.total if self.total >= MIN_TOTAL_FOR_SHARE else target

    def steer(self, target: float) -> None:
        """One decision point: nudge the bias towards the share she is meant to have."""
        self.bias = min(
            BIAS_RANGE[1],
            max(BIAS_RANGE[0], self.bias + BIAS_RATE * (target - self.share_now(target))),
        )

    def said(self, lines: int, contagion: float) -> None:
        self.her += lines
        self.total += lines
        self.heat += contagion * lines

    def heard(self) -> None:
        self.total += 1


def recovered_energy(energy: float, seconds: float, stamina_minutes: float) -> float:
    return 100.0 - (100.0 - energy) * math.exp(-seconds / (stamina_minutes * 60))


def breadth(energy: float) -> float:
    return max(energy / 100.0, MIN_BREADTH)


def chat_logit(
    state: TalkState,
    *,
    mention: bool,
    reply: bool,
    question: bool,
    affinity: float,
    arousal: float,
    energy: float,
    count: int,
) -> float:
    """The log-odds of speaking up on a batch of `count` new messages; `affinity` is 0-100."""
    return (
        state.bias
        + MENTION_WEIGHT * mention
        + REPLY_WEIGHT * reply
        + QUESTION_WEIGHT * question
        + AFFINITY_WEIGHT * min(AFFINITY_RANGE[1], max(AFFINITY_RANGE[0], affinity / 100.0))
        + AROUSAL_WEIGHT * arousal
        + math.log(breadth(energy))
        + state.heat
        + COUNT_WEIGHT * math.log(count)
    )


def probability(z: float, temperature: float) -> float:
    x = -z / temperature
    return 0.0 if x > 700 else 1.0 / (1.0 + math.exp(x))


def topic_probability(urge: float, energy: float) -> float:
    return TOPIC_RATE * urge * urge * breadth(energy)


_QUESTION_RE = re.compile(
    "[?？]|什么|怎么|为什么|为啥|咋|啥|如何|哪|吗|么|呢|能不能|是不是|有没有|会不会|可不可以"
)


def is_question(text: str) -> bool:
    return _QUESTION_RE.search(text) is not None
