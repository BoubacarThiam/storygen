"""Tests de steps/images.py.

Unitaires (aucun réseau) + un vrai test réseau contre Pollinations.ai
(image minuscule, pour rester rapide) qui vérifie le constat réel du
projet : l'endpoint renvoie du JPEG, jamais du PNG.
"""

from __future__ import annotations

import unittest
import urllib.parse

from storygen.models import Story
from storygen.steps.images import (
    _dimensions_for_format,
    _is_valid_image,
    build_full_image_prompt,
    build_pollinations_url,
)
from storygen.utils.http import download_binary

STORY = Story.model_validate(
    {
        "title": "T",
        "logline": "L",
        "style_bible": {"art_style": "warm watercolor", "palette": "blue, ochre", "seed": 7},
        "characters": [
            {"id": "a", "physical_description": "old fisherman, blue boubou"},
            {"id": "b", "physical_description": "young boy, green shirt"},
        ],
        "scenes": [
            {
                "index": 1,
                "narration": "Une phrase.",
                "image_prompt": "a boat on the sea at dawn",
                "characters_present": ["a"],
                "duration_hint": 5.0,
            }
        ],
    }
)


class TestBuildFullImagePrompt(unittest.TestCase):
    def test_concatenates_scene_character_and_style(self) -> None:
        prompt = build_full_image_prompt(STORY, STORY.scenes[0])
        self.assertIn("a boat on the sea at dawn", prompt)
        self.assertIn("old fisherman, blue boubou", prompt)
        self.assertIn("warm watercolor", prompt)
        self.assertIn("blue, ochre", prompt)
        # personnage absent de la scène -> ne doit pas apparaître
        self.assertNotIn("young boy", prompt)

    def test_truncates_when_too_long(self) -> None:
        prompt = build_full_image_prompt(STORY, STORY.scenes[0], max_len=20)
        self.assertLessEqual(len(prompt), 20)

    def test_motion_uses_motion_prompt_when_present(self) -> None:
        story = STORY.model_copy(deep=True)
        story.scenes[0].motion_prompt = "the fisherman lifts the bottle, water splashes"
        prompt = build_full_image_prompt(story, story.scenes[0], motion=True)
        self.assertIn("lifts the bottle", prompt)
        self.assertIn("old fisherman, blue boubou", prompt)
        self.assertIn("warm watercolor", prompt)

    def test_motion_falls_back_to_suffix_without_motion_prompt(self) -> None:
        prompt = build_full_image_prompt(STORY, STORY.scenes[0], motion=True)
        self.assertIn("a moment later", prompt)
        self.assertIn("a boat on the sea at dawn", prompt)


class TestBuildPollinationsUrl(unittest.TestCase):
    def test_url_contains_encoded_prompt_and_params(self) -> None:
        url = build_pollinations_url("a red boat & sea", seed=42, width=100, height=200)
        self.assertIn(urllib.parse.quote("a red boat & sea", safe=""), url)
        self.assertIn("seed=42", url)
        self.assertIn("width=100", url)
        self.assertIn("height=200", url)
        self.assertIn("nologo=true", url)
        self.assertIn("model=flux", url)


class TestDimensionsForFormat(unittest.TestCase):
    def test_9_16_bigger_than_target_for_ken_burns(self) -> None:
        w, h = _dimensions_for_format("9:16")
        self.assertGreater(w, 1080)
        self.assertGreater(h, 1920)
        self.assertEqual(w % 2, 0)
        self.assertEqual(h % 2, 0)

    def test_unknown_format_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _dimensions_for_format("4:3")


class TestIsValidImage(unittest.TestCase):
    def test_rejects_empty(self) -> None:
        self.assertFalse(_is_valid_image(b""))

    def test_rejects_non_jpeg(self) -> None:
        self.assertFalse(_is_valid_image(b"<html>error</html>" * 1000))

    def test_accepts_jpeg_signature_with_enough_size(self) -> None:
        fake = b"\xff\xd8\xff" + b"\x00" * 4000
        self.assertTrue(_is_valid_image(fake))


class TestPollinationsLive(unittest.TestCase):
    """Vrai appel réseau (256x256, assez petit pour rester rapide mais assez
    grand pour dépasser MIN_IMAGE_SIZE comme le ferait une vraie image de
    scène) — confirme le contrat réel de Pollinations : JPEG, pas PNG, quels
    que soient les paramètres, et qu'un User-Agent par défaut est requis
    (Cloudflare renvoie 403 sur le User-Agent par défaut d'urllib)."""

    def test_real_download_returns_valid_jpeg(self) -> None:
        url = build_pollinations_url("a red apple on white background", seed=1, width=256, height=256)
        data = download_binary(url, timeout=60.0, retries=3)
        self.assertTrue(_is_valid_image(data), "réponse Pollinations non reconnue comme JPEG valide")


if __name__ == "__main__":
    unittest.main()
