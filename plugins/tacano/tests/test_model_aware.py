"""Deterministic, offline tests for the model-aware cost model.
Run: python -m unittest discover -s plugins/tacano/tests   (from the repo root)
Never calls Jev or any API, never touches ~/.claude."""
import datetime as dt
import json
import os
import pathlib
import sys
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="tacano-test-")
os.environ["CLAUDE_PLUGIN_DATA"] = os.path.join(_TMP, "data")       # jevlib creates this dir on import
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import jevlib as J      # noqa: E402
import prices as PR     # noqa: E402
import report as R      # noqa: E402

OPUS, SONNET, HAIKU, FABLE = "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-2"
PROBS = {"none": .1, "few": .2, "moderate": .3, "heavy": .4}


def answers(sonnet=.8, haiku=.1, keep=.1, intent="build", sensitive=0.0, probs=PROBS):
    return {"model_fit": {"probabilities": {"haiku": haiku, "sonnet": sonnet, "keep_opus": keep}},
            "sensitive": {"noul": sensitive}, "intent": {"choice": intent}, "volume": {"probabilities": probs}}


def haiku_answers():
    return answers(sonnet=.1, haiku=.9, keep=0.0, intent="lookup")


def cfg_old_prices():
    """Config with the PRE-change sonnet read rate (0.20), to reproduce the old numbers exactly."""
    cfg = dict(J.DEFAULTS)
    cfg["prices"] = PR.merge_prices({"sonnet": {"read": 0.20}})
    cfg["_fut"] = 20.0
    return cfg


class Matcher(unittest.TestCase):
    def test_ids(self):
        for mid, fam in ((SONNET, "sonnet"), (FABLE, "fable"), (OPUS, "opus"), (HAIKU, "haiku"),
                         ("claude-haiku-4-5-20251001", "haiku-4"), ("CLAUDE-SONNET-5-5", "sonnet"),
                         ("claude-mythos-5-2", "fable")):
            self.assertEqual(PR.family(mid), fam, mid)

    def test_unknown_and_synthetic_fall_back_to_opus(self):
        for mid in ("<synthetic>", "gpt-9", "", None):
            self.assertIsNone(PR.family(mid))
            self.assertEqual(PR.base_family(mid), "opus")
            self.assertEqual(PR.price(mid), PR.price(OPUS))

    def test_verified_values(self):
        self.assertEqual(PR.price(OPUS), (4, 20, 0.20, 8))
        self.assertEqual(PR.price(SONNET), (2, 10, 0.10, 4))
        self.assertEqual(PR.price(FABLE), (8, 40, 0.20, 16))
        self.assertEqual(PR.price(HAIKU), (0.10, 0.50, 0.01, 0.20))
        self.assertEqual(PR.price(HAIKU, 100_001), (0.50, 2.50, 0.05, 1.00))   # long tier
        self.assertEqual(PR.price("claude-haiku-4-5"), (1, 5, 0.10, 2))

    def test_override_merge_keeps_old_format(self):
        before = json.dumps(PR.PRICES, sort_keys=True)
        m = PR.merge_prices({"opus": {"out": 21}, "haiku": {"long": {"out": 3}}, "bogus": 5})
        self.assertEqual(m["opus"]["out"], 21)
        self.assertEqual(m["opus"]["in"], 4)                     # old partial configs lack "in": defaults fill it
        self.assertEqual(m["haiku"]["long"]["out"], 3)
        self.assertEqual(m["haiku"]["long"]["read"], 0.05)
        self.assertEqual(m["fable"], PR.PRICES["fable"])         # families absent from the override still exist
        self.assertEqual(json.dumps(PR.PRICES, sort_keys=True), before)   # defaults not mutated


class Routing(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(J.DEFAULTS)
        self.cfg["prices"] = PR.default_prices()

    def route(self, ans, base, ctx=100_000):
        return J.route(self.cfg, ans, ctx, base)

    def test_opus_base(self):
        w, ev, _ = self.route(answers(), OPUS)
        self.assertIn(w, ("sonnet", "haiku"))
        self.assertGreater(ev, 0.05)
        self.assertEqual(self.route(haiku_answers(), OPUS)[0], "haiku")

    def test_sonnet_base_only_haiku(self):
        w, ev, _ = self.route(answers(), SONNET)           # used to give a sonnet worker
        self.assertIsNone(w)
        self.assertEqual(ev, 0.0)
        w, ev, _ = self.route(haiku_answers(), SONNET)
        self.assertEqual(w, "haiku")
        self.assertGreater(ev, 0.05)
        # the haiku candidate still needs haiku_threshold + intent
        self.assertIsNone(self.route(answers(sonnet=.1, haiku=.9, keep=0, intent="build"), SONNET)[0])

    def test_haiku_base_never_delegates(self):
        for base in (HAIKU, "claude-haiku-4-5"):
            for a in (answers(), haiku_answers()):
                self.assertIsNone(self.route(a, base)[0])

    def test_fable_base_both(self):
        w, ev, _ = self.route(answers(), FABLE)
        self.assertEqual(w, "sonnet")
        self.assertGreater(ev, 0.05)
        w, ev, _ = self.route(haiku_answers(), FABLE)
        self.assertEqual(w, "haiku")
        self.assertGreater(ev, 0.05)

    def test_unknown_base_is_opus(self):
        for b in (None, "<synthetic>", "mystery"):
            self.assertEqual(self.route(answers(), b), self.route(answers(), OPUS))

    def test_sensitive_and_keep_opus_rules_hold_for_all_bases(self):
        for base in (OPUS, SONNET, HAIKU, FABLE):
            self.assertEqual(self.route(answers(sensitive=.5), base)[2], "sensitive")
            self.assertEqual(self.route(answers(keep=.5), base)[2], "keep_opus")

    def test_expected_saving_signs(self):
        c = self.cfg
        c["_fut"] = 20.0

        def es(m, b):
            return J.expected_saving(c, PROBS, 100_000, m, b)
        self.assertGreater(es("haiku", OPUS), 0)
        self.assertGreater(es("haiku", SONNET), 0)
        self.assertGreater(es("sonnet", FABLE), 0)
        self.assertLess(es("sonnet", SONNET), 0)     # same-price worker only adds overhead
        self.assertLess(es("sonnet", HAIKU), 0)      # a worker pricier than the base never pays
        self.assertGreater(es("haiku", FABLE), es("haiku", OPUS))
        self.assertGreater(es("haiku", OPUS), es("haiku", SONNET))


class OpusRegression(unittest.TestCase):
    """Numbers produced by the code BEFORE this change (HEAD jevlib.py), base Opus. The sonnet-worker cases use
    the old 0.20 sonnet cache-read rate via override: only the (corrected) price changed, not the model."""
    def test_numbers(self):
        cfg = cfg_old_prices()
        t = J._turn_cost
        self.assertAlmostEqual(t(cfg, 100000, 5, "sonnet", False), 0.2378, 9)
        self.assertAlmostEqual(t(cfg, 100000, 5, "sonnet", True), 0.3072, 9)
        self.assertAlmostEqual(t(cfg, 100000, 5, "haiku", True), 0.09231, 9)
        self.assertAlmostEqual(J.expected_saving(cfg, PROBS, 100000, "sonnet"), 0.013022, 9)
        self.assertAlmostEqual(J.expected_saving(cfg, PROBS, 100000, "haiku"), 0.3380835, 9)
        w, ev, why = J.route(cfg, answers(), 100000, OPUS)
        self.assertEqual((w, why), ("sonnet", "ev"))
        self.assertAlmostEqual(ev, 0.083294, 9)
        w, ev, _ = J.route(cfg, haiku_answers(), 100000)       # default base == opus
        self.assertEqual(w, "haiku")
        self.assertAlmostEqual(ev, 0.4083555, 9)
        self.assertAlmostEqual(J.resume_cost(cfg, 200000, True), 1.6, 9)
        self.assertAlmostEqual(J.resume_cost(cfg, 200000, False), 0.04, 9)
        self.assertAlmostEqual(J.switch_saving(cfg, 300000, "clear"), 3.72, 9)
        self.assertAlmostEqual(J.switch_saving(cfg, 300000, "compact", {"volume": {"probabilities": PROBS}}), 2.52512, 9)

    def test_default_config_prices_keep_old_opus_haiku(self):
        P = dict(J.DEFAULTS)["prices"]
        self.assertEqual((P["opus"]["out"], P["opus"]["read"], P["opus"]["write"]), (20, 0.20, 8))
        self.assertEqual((P["haiku"]["out"], P["haiku"]["read"], P["haiku"]["write"], P["haiku"]["long_from"]),
                         (0.50, 0.01, 0.20, 100_000))
        self.assertEqual(P["haiku"]["long"], {"in": 0.50, "out": 2.50, "read": 0.05, "write": 1.00, "write5m": 0.625})


def _w(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def asst(ts, mid, model, read=0, create=0, inp=0, out=0, side=False, text="hi"):
    return {"type": "assistant", "timestamp": ts, "isSidechain": side,
            "message": {"id": mid, "model": model, "content": [{"type": "text", "text": text}],
                        "usage": {"input_tokens": inp, "cache_read_input_tokens": read,
                                  "cache_creation_input_tokens": create, "output_tokens": out}}}


def human(ts, text="do it"):
    return {"type": "user", "timestamp": ts, "origin": {"kind": "human"}, "message": {"content": text}}


class Transcript(unittest.TestCase):
    def test_model_from_last_real_main_assistant(self):
        p = pathlib.Path(_TMP) / "t1.jsonl"
        _w(p, [human("2026-01-01T10:00:00Z"),
               asst("2026-01-01T10:00:01Z", "a", OPUS, read=1000),
               asst("2026-01-01T10:00:02Z", "b", SONNET, read=2000),
               asst("2026-01-01T10:00:03Z", "c", HAIKU, read=500, side=True),     # sidechain ignored
               asst("2026-01-01T10:00:04Z", "d", "<synthetic>", text="No response requested.")])
        self.assertEqual(J.read_transcript(str(p))["model"], SONNET)

    def test_no_model(self):
        p = pathlib.Path(_TMP) / "t2.jsonl"
        _w(p, [human("2026-01-01T10:00:00Z"), asst("2026-01-01T10:00:01Z", "a", "<synthetic>")])
        self.assertIsNone(J.read_transcript(str(p))["model"])
        self.assertIsNone(J.read_transcript(str(pathlib.Path(_TMP) / "missing.jsonl"))["model"])


class ReportMixed(unittest.TestCase):
    def test_mixed_models_hand_computed(self):
        d = pathlib.Path(tempfile.mkdtemp(dir=_TMP))
        # s1: Sonnet base (after an earlier Opus call and a trailing <synthetic>), Haiku worker.
        _w(d / "s1.jsonl", [human("2026-01-01T10:00:00Z"),
                            asst("2026-01-01T09:59:00Z", "m0", OPUS, read=80000),
                            asst("2026-01-01T10:00:10Z", "m1", SONNET, read=100000, out=100),
                            asst("2026-01-01T10:00:15Z", "m2", "<synthetic>")])
        _w(d / "s1" / "subagents" / "agent-a.jsonl", [
            asst("2026-01-01T10:00:20Z", "w1", HAIKU, read=30000, create=2000, out=500, side=True),
            asst("2026-01-01T10:00:30Z", "w2", HAIKU, read=32000, create=1000, out=300, side=True)])
        # s2: Opus base, Sonnet worker.
        _w(d / "s2.jsonl", [human("2026-01-01T11:00:00Z"), asst("2026-01-01T11:00:10Z", "n1", OPUS, read=50000)])
        _w(d / "s2" / "subagents" / "agent-b.jsonl", [
            asst("2026-01-01T11:00:20Z", "x1", SONNET, read=10000, create=40000, out=2000, side=True)])
        # s3: Sonnet base, /compact block that does not pay off (negative saving must show as is).
        _w(d / "s3.jsonl", [human("2026-01-01T11:55:00Z"),
                            asst("2026-01-01T11:59:00Z", "p1", SONNET, read=300000),
                            {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-01-01T12:05:00Z"},
                            asst("2026-01-01T12:06:00Z", "p2", SONNET, read=60000)])
        log = [{"ts": "2026-01-01T10:00:00+00:00", "session": "s1", "action": "hint_haiku", "ctx": 100000},
               {"ts": "2026-01-01T11:00:00+00:00", "session": "s2", "action": "hint_sonnet", "ctx": 50000},
               {"ts": "2026-01-01T12:00:00+00:00", "session": "s3", "action": "block_compact", "ctx": 300000}]
        cache = {}

        def sess(sid):
            return cache.setdefault(sid, R.load_session(str(d / f"{sid}.jsonl")))
        sv = R.compute_savings(log, sess)
        # s1: counterfactual Sonnet inline = (0.016 + 0.0068) + (0.0102 + 0.0068) = 0.0398; Haiku actual = 0.00095 + 0.00067
        self.assertAlmostEqual(sv["by_base_deleg"]["sonnet"][1], 0.0398 - 0.00162, 9)
        # s2: Opus inline 0.362 (40000+2000+320000 units) vs Sonnet actual 0.181
        self.assertAlmostEqual(sv["by_base_deleg"]["opus"][1], 0.181, 9)
        self.assertEqual((sv["hints"], sv["followed"]), (2, 2))
        self.assertAlmostEqual(sv["deleg_saved"], 0.0398 - 0.00162 + 0.181, 9)
        # s3 on Sonnet: lighter calls 240000*0.10 = 0.024 minus switch (300000*0.10 + 10000*10 + 60000*4) = 0.37
        self.assertEqual((sv["blocks"], sv["done"]), (1, 1))
        self.assertAlmostEqual(sv["comp_saved"], 0.024 - 0.37, 9)
        self.assertLess(sv["comp_saved"], 0)
        self.assertEqual(set(sv["by_base_comp"]), {"sonnet"})

    def test_cost_uses_model_rates(self):
        u = {"input_tokens": 1000, "output_tokens": 1000, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 1000}
        self.assertAlmostEqual(R.cost(u, FABLE), (8000 + 40000 + 200 + 16000) / 1e6, 12)
        self.assertAlmostEqual(R.cost(u, "<synthetic>"), R.cost(u, OPUS), 12)


class FableOpusWorker(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(J.DEFAULTS)
        self.cfg["prices"] = PR.default_prices()

    def test_capability_signal_gives_opus_with_positive_saving(self):
        heavy = {"none": 0, "few": 0, "moderate": .1, "heavy": .9}       # only heavy turns repay the worker's cold start
        a = answers(sonnet=.7, haiku=.1, keep=.2, probs=heavy)             # keep_opus in [0.15, 0.3): intermediate band
        w, ev, why = J.route(self.cfg, a, 100_000, FABLE)
        self.assertEqual((w, why), ("opus", "ev"))
        self.assertGreater(ev, 0.05)
        # Hand calculation (prices.py: Fable 8/40/.20/16, Opus 4/20/.20/8), ctx 100k, fut = (250k-100k)/1600 = 93.75 -> cap 52
        g, fut, out = 1600, 52, 450

        def saving(k):
            inline = sum((100_000 + i * g) * .20 + g * 16 + out * 40 for i in range(k)) + g * (k - 1) * fut * .20
            deleg = (2 * 100_000 * .20 + 700 * 40 + 1500 * 16 + out * 40 + 30_000 * 8
                     + sum((30_000 + j * g) * .20 + g * 8 + out * 20 for j in range(round(k * 1.3)))
                     + 1500 * fut * .20)
            return (inline - deleg) / 1e6
        self.assertAlmostEqual(ev, .1 * saving(5) + .9 * saving(17), 9)
        self.assertGreater(saving(17), 0)

    def test_no_signal_gives_sonnet(self):
        self.assertEqual(self.route_w(answers(sonnet=.8, haiku=.1, keep=.1)), "sonnet")

    def test_haiku_signal_still_wins(self):
        self.assertEqual(self.route_w(answers(sonnet=.0, haiku=.9, keep=.2, intent="lookup")), "haiku")

    def test_keep_opus_threshold_still_blocks(self):
        self.assertIsNone(self.route_w(answers(keep=.3)))

    def test_never_opus_worker_under_other_bases(self):
        for base in (OPUS, SONNET, HAIKU):
            self.assertNotEqual(self.route_w(answers(sonnet=.7, keep=.2), base), "opus")

    def route_w(self, a, base=FABLE):
        return J.route(self.cfg, a, 100_000, base)[0]

    def test_report_followed_under_fable_not_under_opus(self):
        d = pathlib.Path(tempfile.mkdtemp(dir=_TMP))
        for sid, base, t in (("f", FABLE, "10"), ("o", OPUS, "11")):
            _w(d / f"{sid}.jsonl", [human(f"2026-01-01T{t}:00:00Z"), asst(f"2026-01-01T{t}:00:10Z", "b" + sid, base, read=50000)])
            _w(d / sid / "subagents" / "agent-a.jsonl", [
                asst(f"2026-01-01T{t}:00:20Z", "w" + sid, OPUS, read=10000, create=40000, out=2000, side=True)])
        log = [{"ts": f"2026-01-01T{t}:00:00+00:00", "session": s, "action": "hint_opus", "ctx": 50000}
               for s, t in (("f", "10"), ("o", "11"))]
        cache = {}
        sv = R.compute_savings(log, lambda sid: cache.setdefault(sid, R.load_session(str(d / f"{sid}.jsonl"))))
        self.assertEqual((sv["hints"], sv["followed"]), (2, 1))          # opus-under-opus is not a delegation
        self.assertEqual(set(sv["by_base_deleg"]), {"fable"})
        # worker (Opus) = 2000*20 + 10000*0.2 + 40000*8 = 362000 ; Fable inline = 2000*40 + 10000*0.2 + 40000*16 = 722000
        self.assertAlmostEqual(sv["deleg_saved"], (722_000 - 362_000) / 1e6, 9)


if __name__ == "__main__":
    unittest.main()
