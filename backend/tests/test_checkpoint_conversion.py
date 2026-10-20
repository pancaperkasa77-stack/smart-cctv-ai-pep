"""Converter rejects invalid inputs before importing/deserializing Torch."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.ai.convert_apd_weights import main


class ConversionInputTests(unittest.TestCase):
    def test_existing_output_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "model.safetensors"
            out.write_bytes(b"keep")
            argv = ["convert", "--source", str(Path(folder)/"missing.pt"), "--output", str(out),
                    "--expected-sha256", "0" * 64]
            with patch("sys.argv", argv), self.assertRaises(SystemExit):
                main()
            self.assertEqual(out.read_bytes(), b"keep")

    def test_hash_mismatch_rejected_before_loading(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.pt"
            source.write_bytes(b"not a checkpoint")
            out = Path(folder) / "model.safetensors"
            argv = ["convert", "--source", str(source), "--output", str(out),
                    "--expected-sha256", "0" * 64]
            with patch("sys.argv", argv), self.assertRaises(SystemExit):
                main()
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
