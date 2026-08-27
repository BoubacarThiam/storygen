"""Test réel de steps/voice.py contre le vrai service edge-tts (aucune clé
requise, gratuit et illimité). Génère une narration courte et vérifie que
le MP3 produit est valide et que sa durée réelle est mesurable."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storygen.config import DEFAULT_VOICE
from storygen.models import Story
from storygen.steps.voice import generate_voices
from storygen.utils.cache import is_fresh

STORY = Story.model_validate(
    {
        "title": "T",
        "logline": "L",
        "style_bible": {"art_style": "x", "palette": "y", "seed": 1},
        "characters": [],
        "scenes": [
            {
                "index": 1,
                "narration": "Ceci est un test de voix off en français.",
                "image_prompt": "x",
                "characters_present": [],
                "duration_hint": 3.0,
            }
        ],
    }
)


class TestGenerateVoicesLive(unittest.TestCase):
    def test_real_edge_tts_synthesis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            story_dir = Path(tmp)
            durations = generate_voices(STORY, story_dir, voice=DEFAULT_VOICE, force=True)

            self.assertIn(1, durations)
            self.assertGreater(durations[1], 0.5)

            mp3 = story_dir / "assets" / "audio" / "scene_01.mp3"
            self.assertTrue(is_fresh(mp3, min_size=500))

    def test_cache_hit_skips_regeneration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            story_dir = Path(tmp)
            generate_voices(STORY, story_dir, voice=DEFAULT_VOICE, force=True)
            mp3 = story_dir / "assets" / "audio" / "scene_01.mp3"
            first_mtime = mp3.stat().st_mtime_ns

            generate_voices(STORY, story_dir, voice=DEFAULT_VOICE, force=False)
            self.assertEqual(mp3.stat().st_mtime_ns, first_mtime, "le cache aurait dû éviter la régénération")


if __name__ == "__main__":
    unittest.main()
