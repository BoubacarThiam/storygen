"""Tests réels de utils/ffmpeg.py — génère de vrais fichiers avec ffmpeg
(lavfi, aucun réseau requis) et vérifie le comportement réel de probe_duration
et run_ffmpeg (utilisés par steps/voice.py et steps/montage.py)."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from storygen.utils.ffmpeg import FfmpegError, probe_duration, run_ffmpeg


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


class TestRunFfmpeg(unittest.TestCase):
    def test_runs_real_command_successfully(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.mp3"
            run_ffmpeg([
                "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                "-t", "1", "-q:a", "9", str(path),
            ])
            self.assertTrue(path.is_file())
            self.assertAlmostEqual(probe_duration(path), 1.0, delta=0.1)

    def test_bad_args_raise_ffmpeg_error_with_stderr(self) -> None:
        with self.assertRaises(FfmpegError) as ctx:
            run_ffmpeg(["-i", "/nonexistent/does-not-exist.mp4", "/tmp/wont-be-created.mp4"])
        self.assertTrue(str(ctx.exception))  # le stderr ffmpeg doit être remonté, pas juste le code retour


if __name__ == "__main__":
    unittest.main()
