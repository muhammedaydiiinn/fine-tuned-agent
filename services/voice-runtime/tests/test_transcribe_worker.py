import pathlib
import sys
from unittest import TestCase

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.transcribe import Seg
from app.transcribe_worker import merge_stereo_segments, mono_segments, _score_agent_channel


def seg(start, end, text, conf=0.9):
    return Seg(start_s=start, end_s=end, text=text, confidence=conf)


class ScoreAgentChannelTests(TestCase):
    def test_script_markers_counted(self):
        segments = [seg(0, 3, "Guten Tag, Anna Weber von Anrufblocker."), seg(4, 8, "Das Gold Paket schützt Sie.")]
        self.assertGreaterEqual(_score_agent_channel(segments), 3)

    def test_customer_channel_scores_zero(self):
        segments = [seg(0, 2, "Hallo?"), seg(5, 7, "Was kostet das?")]
        self.assertEqual(_score_agent_channel(segments), 0)

    def test_markers_after_probe_window_ignored(self):
        segments = [seg(120, 125, "Anrufblocker Gold Paket")]
        self.assertEqual(_score_agent_channel(segments), 0)


class MergeStereoSegmentsTests(TestCase):
    def test_merge_orders_by_time_and_tags_speakers(self):
        agent_channel = [seg(0, 3, "Guten Tag, Anna Weber von Anrufblocker."), seg(6, 9, "14 Tage kostenlos.")]
        customer_channel = [seg(3.5, 5, "Ja, hallo."), seg(9.5, 11, "Okay.")]
        merged, resolved = merge_stereo_segments([agent_channel, customer_channel])
        self.assertTrue(resolved)
        self.assertEqual([m["speaker"] for m in merged], ["agent", "customer", "agent", "customer"])
        self.assertEqual([m["idx"] for m in merged], [0, 1, 2, 3])
        self.assertEqual(merged[0]["start_ms"], 0)
        self.assertEqual(merged[1]["start_ms"], 3500)

    def test_ambiguous_channels_return_null_speakers(self):
        channel_a = [seg(0, 2, "Hallo?")]
        channel_b = [seg(2.5, 4, "Guten Tag.")]
        merged, resolved = merge_stereo_segments([channel_a, channel_b])
        self.assertFalse(resolved)
        self.assertTrue(all(m["speaker"] is None for m in merged))

    def test_second_channel_can_be_agent(self):
        customer_channel = [seg(0, 2, "Hallo?")]
        agent_channel = [seg(2.5, 6, "Anna Weber von Anrufblocker, Gold Paket.")]
        merged, resolved = merge_stereo_segments([customer_channel, agent_channel])
        self.assertTrue(resolved)
        self.assertEqual(merged[0]["speaker"], "customer")
        self.assertEqual(merged[1]["speaker"], "agent")


class MonoSegmentsTests(TestCase):
    def test_mono_segments_have_null_speaker(self):
        rows = mono_segments([seg(0, 1.2, "Hallo?"), seg(2, 4, "Guten Tag.")])
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["speaker"] is None for r in rows))
        self.assertEqual(rows[1]["end_ms"], 4000)
        self.assertEqual(rows[0]["idx"], 0)
