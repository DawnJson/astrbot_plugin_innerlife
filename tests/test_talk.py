from __future__ import annotations

import math
import unittest

import support  # noqa: F401  (puts the plugin's parent on sys.path)
from astrbot_plugin_innerlife import talk
from astrbot_plugin_innerlife.talk import TalkState

QUIET = dict(
    mention=False, reply=False, question=False, scoped=False,
    affinity=0.0, arousal=0.3, energy=60.0, count=1,
)


def logit(state: TalkState | None = None, **features) -> float:
    return talk.chat_logit(state or TalkState(), **{**QUIET, **features})


class GateTests(unittest.TestCase):
    def test_a_mention_or_a_reply_raises_the_chance(self) -> None:
        base = talk.probability(logit(), 1.0)
        self.assertGreater(talk.probability(logit(mention=True), 1.0), base)
        self.assertGreater(talk.probability(logit(reply=True), 1.0), talk.probability(logit(mention=True), 1.0))
        self.assertGreater(talk.probability(logit(question=True, scoped=True), 1.0),
                           talk.probability(logit(question=True), 1.0))

    def test_formula_matches_the_contract(self) -> None:
        state = TalkState(bias=-3.0, heat=0.4)
        z = logit(state, mention=True, question=True, scoped=True, affinity=50.0, energy=50.0, count=4)
        expected = -3.0 + 2.0 + 1.6 + 0.5 + 0.8 * 0.3 + math.log(0.5) + 0.4 + 0.5 * math.log(4)
        self.assertAlmostEqual(z, expected)
        self.assertAlmostEqual(talk.probability(z, 1.0), 1 / (1 + math.exp(-expected)))
        # Affinity is clamped to [-0.6, 1] and tiredness to a floor of 5%.
        self.assertAlmostEqual(logit(affinity=500.0) - logit(affinity=0.0), 1.0)
        self.assertAlmostEqual(logit(affinity=-500.0) - logit(affinity=0.0), -0.6)
        self.assertAlmostEqual(logit(energy=0.0), logit(energy=5.0))

    def test_temperature_spreads_the_odds(self) -> None:
        weak, strong = logit(), logit(mention=True, reply=True)
        self.assertLess(weak, 0)
        self.assertGreater(talk.probability(weak, 2.0), talk.probability(weak, 0.5))
        self.assertLess(talk.probability(strong, 2.0), talk.probability(strong, 0.5))
        self.assertEqual(talk.probability(-1e6, 1.0), 0.0)
        self.assertEqual(talk.probability(1e6, 1.0), 1.0)

    def test_tiredness_and_a_busy_batch(self) -> None:
        self.assertLess(logit(energy=10.0), logit(energy=90.0))
        self.assertGreater(logit(count=8), logit(count=1))
        self.assertGreater(logit(TalkState(heat=1.0)), logit(TalkState(heat=0.0)))

    def test_question_words_count_without_a_question_mark(self) -> None:
        for text in ("怎么配置", "是什么原因", "能不能用", "好用吗", "为啥报错", "有没有人", "哪个版本", "对吧？", "ok?"):
            self.assertTrue(talk.is_question(text), text)
        for text in ("今天好热", "哈哈哈", "我去吃饭了"):
            self.assertFalse(talk.is_question(text), text)


class ShareTests(unittest.TestCase):
    def test_share_below_target_raises_the_bias_and_above_lowers_it(self) -> None:
        quiet = TalkState(her=2.0, total=100.0)
        before = quiet.bias
        quiet.steer(0.10)
        self.assertGreater(quiet.bias, before)
        self.assertAlmostEqual(quiet.bias, before + 0.05 * (0.10 - 0.02))
        loud = TalkState(her=50.0, total=100.0)
        before = loud.bias
        loud.steer(0.10)
        self.assertLess(loud.bias, before)

    def test_little_data_counts_as_on_target_and_bias_stays_in_bounds(self) -> None:
        few = TalkState(her=0.0, total=19.0)
        few.steer(0.10)
        self.assertEqual(few.bias, talk.BIAS_START)
        self.assertEqual(few.share_now(0.10), 0.10)
        low = TalkState(bias=-1.0, her=0.0, total=1000.0)
        for _ in range(10000):
            low.steer(1.0)
        self.assertEqual(low.bias, -1.0)
        high = TalkState(bias=-6.0, her=1000.0, total=1000.0)
        for _ in range(10000):
            high.steer(0.0)
        self.assertEqual(high.bias, -6.0)

    def test_lines_count_and_decay_with_a_one_day_half_life(self) -> None:
        state = TalkState(updated_at=0.0)
        state.heard()
        state.said(2, 0.5)
        self.assertEqual((state.her, state.total, state.heat), (2.0, 3.0, 1.0))
        state.advance(86400.0, awake=True, urge_hours=4.0)
        self.assertAlmostEqual(state.her, 1.0)
        self.assertAlmostEqual(state.total, 1.5)
        warm = TalkState(updated_at=0.0)
        warm.said(1, 1.0)
        warm.advance(240.0, awake=True, urge_hours=4.0)
        self.assertAlmostEqual(warm.heat, math.exp(-1.0))


class EnergyUrgeTests(unittest.TestCase):
    def test_energy_recovers_exponentially(self) -> None:
        self.assertAlmostEqual(talk.recovered_energy(20.0, 45 * 60, 45.0), 100 - 80 / math.e)
        self.assertEqual(talk.recovered_energy(20.0, 0.0, 45.0), 20.0)
        self.assertLess(talk.recovered_energy(20.0, 600.0, 90.0), talk.recovered_energy(20.0, 600.0, 10.0))

    def test_urge_grows_while_awake_and_stands_still_asleep(self) -> None:
        awake = TalkState(urge=0.0, updated_at=0.0)
        awake.advance(4 * 3600.0, awake=True, urge_hours=4.0)
        self.assertAlmostEqual(awake.urge, 1 - 1 / math.e)
        asleep = TalkState(urge=0.2, updated_at=0.0)
        asleep.advance(8 * 3600.0, awake=False, urge_hours=4.0)
        self.assertEqual(asleep.urge, 0.2)
        # Growth composes: two half steps equal one whole step.
        split = TalkState(urge=0.0, updated_at=0.0)
        split.advance(2 * 3600.0, awake=True, urge_hours=4.0)
        split.advance(4 * 3600.0, awake=True, urge_hours=4.0)
        self.assertAlmostEqual(split.urge, awake.urge)

    def test_topic_chance_follows_urge_and_energy(self) -> None:
        self.assertAlmostEqual(talk.topic_probability(1.0, 100.0), 0.0127)
        self.assertAlmostEqual(talk.topic_probability(0.5, 50.0), 0.0127 * 0.25 * 0.5)
        self.assertEqual(talk.topic_probability(0.0, 100.0), 0.0)
        self.assertGreater(talk.topic_probability(0.5, 0.0), 0.0)


if __name__ == "__main__":
    unittest.main()
