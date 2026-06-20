from unittest import TestCase

import numpy as np

from app.segmenter import SegmentationConfig, UtteranceSegmenter


def pcm(value: int, milliseconds: int, sample_rate: int = 16000) -> bytes:
    count = sample_rate * milliseconds // 1000
    return np.full(count, value, dtype=np.int16).tobytes()


class UtteranceSegmenterTests(TestCase):
    def test_emits_after_speech_followed_by_silence(self):
        segmenter = UtteranceSegmenter(
            SegmentationConfig(
                rms_threshold=300,
                min_speech_ms=200,
                end_silence_ms=300,
                preroll_ms=0,
            )
        )

        self.assertIsNone(segmenter.push(pcm(1000, 250)))
        utterance = segmenter.push(pcm(0, 300))

        self.assertIsNotNone(utterance)
        self.assertGreater(len(utterance), 0)

    def test_does_not_emit_for_silence(self):
        segmenter = UtteranceSegmenter(SegmentationConfig())

        for _ in range(10):
            self.assertIsNone(segmenter.push(pcm(0, 100)))

    def test_max_duration_forces_boundary(self):
        segmenter = UtteranceSegmenter(
            SegmentationConfig(
                rms_threshold=300,
                max_speech_ms=500,
                preroll_ms=0,
            )
        )

        self.assertIsNone(segmenter.push(pcm(1000, 250)))
        self.assertIsNotNone(segmenter.push(pcm(1000, 250)))
