"""Test réel de steps/montage.py : construit une story minimale (2 scènes),
génère les assets nécessaires localement avec ffmpeg (image de test +
audio synthétique — aucun réseau requis, montage.py ne se soucie pas de la
provenance des images), un SRT trivial, et vérifie que le montage produit
une vraie vidéo MP4 dont la durée correspond à la somme des durées de
scène, et que le cache fonctionne."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from storygen.models import Scene, Story, StyleBible
from storygen.steps.montage import generate_montage
from storygen.utils.ffmpeg import probe_duration


def _make_story(n: int) -> Story:
    return Story(
        title="T",
        logline="L",
        style_bible=StyleBible(art_style="x", palette="y", seed=1),
        characters=[],
        scenes=[
            Scene(index=i, narration="test", image_prompt="x", duration_hint=2.0)
            for i in range(1, n + 1)
        ],
    )


def _make_test_image(path: Path, size: str = "320x568") -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:duration=1:rate=1",
            "-frames:v", "1", str(path),
        ],
        capture_output=True, check=True,
    )


def _make_test_audio(path: Path, duration: float) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}", "-q:a", "9", str(path)],
        capture_output=True, check=True,
    )


class TestGenerateMontageLive(unittest.TestCase):
    def test_builds_real_video_matching_total_duration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            story_dir = Path(tmp) / "story"
            img_dir = story_dir / "assets" / "img"
            audio_dir = story_dir / "assets" / "audio"
            img_dir.mkdir(parents=True)
            audio_dir.mkdir(parents=True)

            story = _make_story(2)
            durations = {1: 1.5, 2: 1.0}
            for scene in story.scenes:
                _make_test_image(img_dir / f"scene_{scene.index:02d}.jpg")
                _make_test_audio(audio_dir / f"scene_{scene.index:02d}.mp3", durations[scene.index])

            srt_path = story_dir / "assets" / "subtitles.srt"
            srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n", encoding="utf-8")

            with mock.patch("storygen.config.OUTPUT_DIR", Path(tmp) / "output"):
                final_path = generate_montage(story, story_dir, durations, srt_path, fmt="9:16", force=True)

                self.assertTrue(final_path.is_file())
                self.assertAlmostEqual(probe_duration(final_path), sum(durations.values()), delta=0.3)

                mtime_before = final_path.stat().st_mtime_ns
                generate_montage(story, story_dir, durations, srt_path, fmt="9:16", force=False)
                self.assertEqual(final_path.stat().st_mtime_ns, mtime_before, "le cache aurait dû éviter le réencodage")

    def test_missing_asset_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            story_dir = Path(tmp) / "story"
            (story_dir / "assets" / "img").mkdir(parents=True)
            (story_dir / "assets" / "audio").mkdir(parents=True)
            story = _make_story(1)
            srt_path = story_dir / "assets" / "subtitles.srt"
            srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n", encoding="utf-8")

            with mock.patch("storygen.config.OUTPUT_DIR", Path(tmp) / "output"):
                with self.assertRaises(FileNotFoundError):
                    generate_montage(story, story_dir, {1: 2.0}, srt_path, fmt="9:16", force=True)


if __name__ == "__main__":
    unittest.main()
