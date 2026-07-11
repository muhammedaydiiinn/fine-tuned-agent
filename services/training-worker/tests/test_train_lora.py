import json
import pathlib
import sys
from unittest import TestCase

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from jobs import train_lora


class TrainLoraMockTests(TestCase):
    def _dataset(self, tmp):
        path = tmp / "ds.jsonl"
        rows = [{"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "{}"}]}] * 8
        path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        return str(path)

    def test_mock_train_ignores_response_masking_flag(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            tmp = pathlib.Path(d)
            ds = self._dataset(tmp)
            out = str(tmp / "adapter")
            # Flag present but mock path must not require torch/trl.
            result = train_lora.train(
                ds, out,
                {"training_mode": "mock", "epochs": 1, "batch_size": 4, "train_on_responses_only": True},
                progress_cb=None,
            )
            self.assertEqual(result["mode"], "mock")
            self.assertGreater(result["steps"], 0)
            self.assertTrue((pathlib.Path(out) / "adapter_config.json").exists())
            self.assertTrue((pathlib.Path(out) / "adapter_model.safetensors").exists())

    def test_mode_dispatch(self):
        # train() dispatches on training_mode without importing the real stack.
        self.assertTrue(hasattr(train_lora, "_train_mock"))
        self.assertTrue(hasattr(train_lora, "_train_real"))
