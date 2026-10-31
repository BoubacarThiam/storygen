"""Tests de storygen/pipeline.py : images et voix/sous-titres en parallèle."""

from __future__ import annotations

import threading
import unittest
from pathlib import Path
from unittest import mock

from storygen.pipeline import generate_media


class TestGenerateMedia(unittest.TestCase):
    def test_images_and_audio_run_concurrently(self) -> None:
        # Chaque branche attend l'autre : sans parallélisme, la barrière expire.
        barrier = threading.Barrier(2, timeout=5)

        def fake_images(*_a, **_k):
            barrier.wait()
            return [Path("img.jpg")]

        def fake_voices(*_a, **_k):
            barrier.wait()
            return {1: 2.0}

        with (
            mock.patch("storygen.pipeline.generate_images", side_effect=fake_images),
            mock.patch("storygen.pipeline.generate_voices", side_effect=fake_voices),
            mock.patch("storygen.pipeline.generate_subtitles", return_value=Path("s.srt")),
        ):
            images, durations, srt = generate_media(
                mock.Mock(), Path("."), fmt="9:16", voice="v"
            )

        self.assertEqual(images, [Path("img.jpg")])
        self.assertEqual(durations, {1: 2.0})
        self.assertEqual(srt, Path("s.srt"))

    def test_image_failure_propagates(self) -> None:
        with (
            mock.patch("storygen.pipeline.generate_images", side_effect=RuntimeError("boom")),
            mock.patch("storygen.pipeline.generate_voices", return_value={1: 2.0}),
            mock.patch("storygen.pipeline.generate_subtitles", return_value=Path("s.srt")),
        ):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                generate_media(mock.Mock(), Path("."), fmt="9:16", voice="v")

    def test_audio_failure_propagates(self) -> None:
        with (
            mock.patch("storygen.pipeline.generate_images", return_value=[]),
            mock.patch("storygen.pipeline.generate_voices", side_effect=RuntimeError("tts")),
        ):
            with self.assertRaisesRegex(RuntimeError, "tts"):
                generate_media(mock.Mock(), Path("."), fmt="9:16", voice="v")


if __name__ == "__main__":
    unittest.main()
