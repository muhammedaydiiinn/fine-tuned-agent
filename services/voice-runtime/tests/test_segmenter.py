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

    def test_speech_started_is_emitted_once_per_utterance(self):
        segmenter = UtteranceSegmenter(
            SegmentationConfig(rms_threshold=300, preroll_ms=0)
        )

        segmenter.push(pcm(1000, 100))
        self.assertTrue(segmenter.consume_speech_started())
        self.assertFalse(segmenter.consume_speech_started())
        self.assertTrue(segmenter.speech_active)

    # ------------------------------------------------------------------
    # snapshot() and speech_ms (M8 Aşama 3)
    # ------------------------------------------------------------------

    def test_snapshot_returns_none_before_speech(self):
        segmenter = UtteranceSegmenter(SegmentationConfig(rms_threshold=300))
        self.assertIsNone(segmenter.snapshot())

    def test_snapshot_returns_accumulated_pcm_without_flushing(self):
        segmenter = UtteranceSegmenter(
            SegmentationConfig(rms_threshold=300, max_speech_ms=60000, preroll_ms=0)
        )
        segmenter.push(pcm(1000, 100))
        snap = segmenter.snapshot()
        self.assertIsNotNone(snap)
        # Still active — snapshot must not flush
        self.assertTrue(segmenter.speech_active)

    def test_speech_ms_grows_with_accumulated_audio(self):
        segmenter = UtteranceSegmenter(
            SegmentationConfig(rms_threshold=300, max_speech_ms=60000, preroll_ms=0)
        )
        segmenter.push(pcm(1000, 200))
        ms_after_200 = segmenter.speech_ms
        segmenter.push(pcm(1000, 100))
        ms_after_300 = segmenter.speech_ms
        self.assertGreater(ms_after_300, ms_after_200)

    # ------------------------------------------------------------------
    # Adaptive VAD (M8 Aşama 1)
    # ------------------------------------------------------------------

    def test_legacy_mode_unchanged(self):
        """adaptive_threshold=False reproduces the original threshold behaviour."""
        segmenter = UtteranceSegmenter(
            SegmentationConfig(
                rms_threshold=300,
                min_speech_ms=200,
                end_silence_ms=300,
                preroll_ms=0,
                adaptive_threshold=False,
            )
        )
        self.assertIsNone(segmenter.push(pcm(1000, 250)))
        self.assertIsNotNone(segmenter.push(pcm(0, 300)))

    def test_adaptive_mode_ignores_low_constant_noise(self):
        """Ambient noise below noise_floor * margin must not trigger speech."""
        # noise_init_rms=200, margin=2.5 → enter=500; pcm(150,...) RMS≈150 < 500
        segmenter = UtteranceSegmenter(
            SegmentationConfig(
                adaptive_threshold=True,
                noise_floor_margin=2.5,
                noise_ema_alpha=0.05,
                absolute_floor_rms=100,
                noise_init_rms=200.0,
                preroll_ms=0,
                min_speech_ms=200,
                end_silence_ms=300,
            )
        )
        for _ in range(5):
            result = segmenter.push(pcm(150, 100))
            self.assertIsNone(result)
        self.assertFalse(segmenter.speech_active)

    def test_adaptive_mode_detects_speech_above_threshold(self):
        """RMS well above margin * noise_floor must open the speech region."""
        # noise_init_rms=200, margin=2.5, absolute_floor=100 → enter=500
        # pcm(3000, ...) RMS≈3000 >> 500
        segmenter = UtteranceSegmenter(
            SegmentationConfig(
                adaptive_threshold=True,
                noise_floor_margin=2.5,
                noise_ema_alpha=0.05,
                absolute_floor_rms=100,
                noise_init_rms=200.0,
                preroll_ms=0,
                min_speech_ms=50,
                end_silence_ms=500,
            )
        )
        segmenter.push(pcm(3000, 100))
        self.assertTrue(segmenter.speech_active)

    def test_adaptive_hysteresis_prevents_immediate_exit(self):
        """After entering speech region, the exit threshold must hold speech open
        even when RMS drops below the *enter* threshold but stays above *exit*."""
        # enter=500, exit=500*0.6=300; mid=400 is between exit and enter
        segmenter = UtteranceSegmenter(
            SegmentationConfig(
                adaptive_threshold=True,
                noise_floor_margin=2.5,
                noise_ema_alpha=0.05,
                absolute_floor_rms=100,
                noise_init_rms=200.0,
                exit_threshold_ratio=0.6,
                preroll_ms=0,
                min_speech_ms=50,
                end_silence_ms=5000,
            )
        )
        segmenter.push(pcm(3000, 100))   # enter speech
        self.assertTrue(segmenter.speech_active)
        segmenter.push(pcm(400, 100))    # mid-level: above exit (300), below enter (500)
        self.assertTrue(segmenter.speech_active)  # still open

    def test_adaptive_floor_reset_after_flush(self):
        """Noise floor and hysteresis state reset after each utterance so
        successive utterances don't inherit stale calibration."""
        cfg = SegmentationConfig(
            adaptive_threshold=True,
            noise_floor_margin=2.5,
            noise_ema_alpha=0.05,
            absolute_floor_rms=100,
            noise_init_rms=200.0,
            preroll_ms=0,
            min_speech_ms=50,
            end_silence_ms=200,
        )
        segmenter = UtteranceSegmenter(cfg)
        segmenter.push(pcm(3000, 100))
        segmenter.push(pcm(0, 300))  # should flush
        # After flush, hysteresis must be off and floor back to noise_init_rms
        self.assertFalse(segmenter._in_speech_region)
        self.assertAlmostEqual(segmenter._noise_floor, 200.0)

    def test_adaptive_digital_silence_clamps_floor(self):
        """Digital silence (RMS≈0) must not drive noise_floor to zero because
        that would make the adaptive threshold zero and trigger on any signal."""
        segmenter = UtteranceSegmenter(
            SegmentationConfig(
                adaptive_threshold=True,
                noise_init_rms=200.0,
                noise_ema_alpha=1.0,  # instant convergence for test speed
                absolute_floor_rms=100,
            )
        )
        segmenter.push(pcm(0, 100))
        self.assertGreaterEqual(segmenter._noise_floor, 1.0)
