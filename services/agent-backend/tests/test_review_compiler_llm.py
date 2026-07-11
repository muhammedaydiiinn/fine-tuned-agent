import pathlib
import sys
from unittest import TestCase, mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core import review_compiler
from app.core.review_compiler import CompiledCorrection, compile_instruction_llm, compile_review

_GOOD_EDITOR = (
    '{"correction_type":"tone_correction","corrected_agent_response":"Gern, ich fasse mich kurz.",'
    '"corrected_next_action":"explain_price","suggestion":"kuerzer & freundlicher","confidence":0.9}'
)


class BackwardCompatTests(TestCase):
    def test_dataclass_defaults(self):
        # Existing 6-field construction still works; new fields default.
        c = CompiledCorrection(
            matched=True, correction_type="tone_correction",
            corrected_agent_response="x", corrected_next_action="",
            matched_rule="r", explanation="e",
        )
        self.assertEqual(c.suggestion, "")
        self.assertEqual(c.source, "deterministic")
        self.assertIn("suggestion", c.as_dict())


class DispatcherTests(TestCase):
    def test_mock_mode_uses_deterministic(self):
        # Default config vllm_mode=mock → LLM path is skipped.
        with mock.patch.object(review_compiler.settings, "review_compiler_mode", "auto"):
            r = compile_review("fiyat yanlış", agent_response="x")
        self.assertEqual(r.source, "deterministic")

    def test_llm_path_none_in_mock(self):
        self.assertIsNone(compile_instruction_llm("fiyat yanlış"))

    def test_llm_editor_success(self):
        with mock.patch.object(review_compiler.settings, "vllm_mode", "real"), \
             mock.patch.object(review_compiler.settings, "review_compiler_mode", "auto"), \
             mock.patch("app.core.model_runtime.production_serving_target",
                        return_value={"mode": "real", "base_url": "x", "model_name": "prod"}), \
             mock.patch("app.core.vllm_client.chat", return_value=_GOOD_EDITOR):
            r = compile_review("kısa ve kibar", agent_response="Lange Antwort. Zweiter Satz.")
        self.assertEqual(r.source, "llm")
        self.assertEqual(r.correction_type, "tone_correction")
        self.assertEqual(r.corrected_agent_response, "Gern, ich fasse mich kurz.")

    def test_low_confidence_falls_back(self):
        low = _GOOD_EDITOR.replace("0.9", "0.1")
        with mock.patch.object(review_compiler.settings, "vllm_mode", "real"), \
             mock.patch.object(review_compiler.settings, "review_compiler_mode", "auto"), \
             mock.patch("app.core.model_runtime.production_serving_target",
                        return_value={"mode": "real", "base_url": "x", "model_name": "prod"}), \
             mock.patch("app.core.vllm_client.chat", return_value=low):
            r = compile_review("fiyat yanlış", agent_response="x")
        self.assertEqual(r.source, "deterministic")

    def test_unknown_correction_type_coerced(self):
        weird = _GOOD_EDITOR.replace("tone_correction", "made_up_type")
        with mock.patch.object(review_compiler.settings, "vllm_mode", "real"), \
             mock.patch("app.core.model_runtime.production_serving_target",
                        return_value={"mode": "real", "base_url": "x", "model_name": "prod"}), \
             mock.patch("app.core.vllm_client.chat", return_value=weird):
            r = compile_instruction_llm("kısa yap", agent_response="x")
        self.assertEqual(r.correction_type, "response_correction")
