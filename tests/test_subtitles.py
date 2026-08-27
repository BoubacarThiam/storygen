"""Tests de steps/subtitles.py.

TestFormatTimestamp / TestWrapSubtitleText / TestBuildSrt : logique pure,
aucun réseau. TestGenerateSubtitlesLive : vraie transcription faster-whisper
sur un vrai MP3 parlé produit par edge-tts (steps/voice.py) — aucune clé
requise, mais télécharge le modèle "small" au premier lancement (voir
README, section Tests)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storygen.config import DEFAULT_VOICE
from storygen.models import Story
from storygen.steps.subtitles import _format_timestamp, build_srt, generate_subtitles, wrap_subtitle_text
from storygen.steps.voice import generate_voices


class TestFormatTimestamp(unittest.TestCase):
    def test_formats_hours_minutes_seconds_millis(self) -> None:
        self.assertEqual(_format_timestamp(0), "00:00:00,000")
        self.assertEqual(_format_timestamp(65.25), "00:01:05,250")
        self.assertEqual(_format_timestamp(3661.001), "01:01:01,001")

    def test_clamps_negative_to_zero(self) -> None:
        self.assertEqual(_format_timestamp(-1.0), "00:00:00,000")


class TestWrapSubtitleText(unittest.TestCase):
    def test_short_text_stays_on_one_line(self) -> None:
        self.assertEqual(wrap_subtitle_text("Bonjour le monde"), "Bonjour le monde")

    def test_long_text_wraps_without_cutting_words(self) -> None:
        text = "Sur les rives de Saint-Louis, au Sénégal, le vieil Ibrahima répare ses filets."
        wrapped = wrap_subtitle_text(text, max_chars=30)
        for line in wrapped.split("\n"):
            self.assertLessEqual(len(line), 30)
        self.assertEqual(wrapped.replace("\n", " "), text)

    def test_empty_text_returns_empty(self) -> None:
        self.assertEqual(wrap_subtitle_text(""), "")


class TestBuildSrt(unittest.TestCase):
    def test_produces_valid_srt_blocks(self) -> None:
        srt = build_srt([(0.0, 1.5, "Bonjour"), (1.5, 3.0, "le monde")])
        self.assertIn("1\n00:00:00,000 --> 00:00:01,500\nBonjour", srt)
        self.assertIn("2\n00:00:01,500 --> 00:00:03,000\nle monde", srt)


STORY = Story.model_validate(
    {
        "title": "T",
        "logline": "L",
        "style_bible": {"art_style": "x", "palette": "y", "seed": 1},
        "characters": [],
        "scenes": [
            {
                "index": 1,
                "narration": "Ceci est un test de sous-titres en français.",
                "image_prompt": "x",
                "characters_present": [],
                "duration_hint": 3.0,
            }
        ],
    }
)


class TestGenerateSubtitlesLive(unittest.TestCase):
    def test_transcribes_real_audio_and_caches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            story_dir = Path(tmp)
            # Vraie narration edge-tts (Phase 2b) plutôt qu'un signal audio
            # arbitraire : la transcription (avec vad_filter) ne produit
            # rien sur un ton sans parole, ce qui ferait échouer le test
            # pour une mauvaise raison.
            durations = generate_voices(STORY, story_dir, voice=DEFAULT_VOICE, force=True)

            srt_path = generate_subtitles(STORY, story_dir, durations)
            self.assertTrue(srt_path.is_file())
            content = srt_path.read_text(encoding="utf-8")
            self.assertIn("-->", content)

            mtime_before = srt_path.stat().st_mtime_ns
            generate_subtitles(STORY, story_dir, durations, force=False)
            self.assertEqual(srt_path.stat().st_mtime_ns, mtime_before, "le cache aurait dû éviter la retranscription")

    def test_missing_audio_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            story_dir = Path(tmp)
            (story_dir / "assets" / "audio").mkdir(parents=True)
            with self.assertRaises(FileNotFoundError):
                generate_subtitles(STORY, story_dir, durations={1: 3.0})


if __name__ == "__main__":
    unittest.main()
