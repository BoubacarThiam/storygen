"""Test réel de utils/ffmpeg.probe_duration — génère un vrai fichier audio
avec ffmpeg (lavfi anullsrc, aucun réseau requis) et vérifie que la durée
mesurée correspond à ce qui a été demandé."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from storygen.utils.ffmpeg import FfmpegError, probe_duration


class TestProbeDuration(unittest.TestCase):
    def test_measures_real_generated_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "silence.mp3"
            subprocess.run(
                [
                    "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                    "-t", "2.5", "-q:a", "9", str(path),
                ],
                capture_output=True, check=True,
            )
            duration = probe_duration(path)
            self.assertAlmostEqual(duration, 2.5, delta=0.1)

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(FfmpegError):
            probe_duration(Path("/nonexistent/does-not-exist.mp3"))


if __name__ == "__main__":
    unittest.main()
