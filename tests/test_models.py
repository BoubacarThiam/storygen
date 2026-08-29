"""Tests du schéma Pydantic de story.json (steps 'unitaires', aucun réseau)."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from storygen.models import SEED_MAX, StyleBible, Story

VALID_STORY = {
    "title": "La bouteille du pêcheur",
    "logline": "Un pêcheur de Saint-Louis trouve une bouteille à la mer.",
    "style_bible": {
        "art_style": "warm watercolor illustration, soft cinematic lighting",
        "palette": "ochre, deep blue, sand tones",
        "seed": 123456,
    },
    "characters": [
        {
            "id": "pecheur",
            "physical_description": "middle-aged Senegalese fisherman, weathered face, "
            "faded blue boubou, straw hat",
        }
    ],
    "scenes": [
        {
            "index": 1,
            "narration": "Chaque matin, Moussa partait en mer avant le lever du soleil, "
            "seul avec ses filets.",
            "image_prompt": "small wooden pirogue on calm ocean at dawn, orange sky",
            "characters_present": ["pecheur"],
            "duration_hint": 6.5,
        },
        {
            "index": 2,
            "narration": "Ce jour-là, quelque chose flottait entre les vagues, "
            "une bouteille scellée de cire.",
            "image_prompt": "a corked glass bottle floating on gentle waves, close up",
            "characters_present": [],
            "duration_hint": 5.0,
        },
    ],
}


class TestStorySchema(unittest.TestCase):
    def test_valid_story_parses(self) -> None:
        story = Story.model_validate(VALID_STORY)
        self.assertEqual(story.title, "La bouteille du pêcheur")
        self.assertEqual(len(story.scenes), 2)
        self.assertAlmostEqual(story.total_duration_hint, 11.5)

    def test_missing_required_field_rejected(self) -> None:
        bad = {k: v for k, v in VALID_STORY.items() if k != "logline"}
        with self.assertRaises(ValidationError):
            Story.model_validate(bad)

    def test_unknown_character_reference_rejected(self) -> None:
        bad = {
            **VALID_STORY,
            "scenes": [
                {**VALID_STORY["scenes"][0], "characters_present": ["fantome"]},
                VALID_STORY["scenes"][1],
            ],
        }
        with self.assertRaises(ValidationError):
            Story.model_validate(bad)

    def test_non_positive_duration_rejected(self) -> None:
        bad = {
            **VALID_STORY,
            "scenes": [
                {**VALID_STORY["scenes"][0], "duration_hint": 0},
                VALID_STORY["scenes"][1],
            ],
        }
        with self.assertRaises(ValidationError):
            Story.model_validate(bad)

    def test_duplicate_scene_index_rejected(self) -> None:
        bad = {
            **VALID_STORY,
            "scenes": [
                VALID_STORY["scenes"][0],
                {**VALID_STORY["scenes"][1], "index": 1},
            ],
        }
        with self.assertRaises(ValidationError):
            Story.model_validate(bad)


class TestSeedClamping(unittest.TestCase):
    """Constat réel : Pollinations renvoie un 500 (pas une erreur propre)
    dès que ``seed`` dépasse un entier signé 32 bits — un modèle :free a
    produit 3081976424, faisant échouer toute la génération d'images."""

    def test_seed_within_range_is_unchanged(self) -> None:
        self.assertEqual(StyleBible(art_style="x", palette="y", seed=48231).seed, 48231)

    def test_seed_above_int32_max_is_clamped(self) -> None:
        seed = StyleBible(art_style="x", palette="y", seed=3081976424).seed
        self.assertLessEqual(seed, SEED_MAX)
        self.assertGreaterEqual(seed, 0)

    def test_negative_seed_is_clamped_to_non_negative(self) -> None:
        seed = StyleBible(art_style="x", palette="y", seed=-5).seed
        self.assertGreaterEqual(seed, 0)
        self.assertLessEqual(seed, SEED_MAX)


if __name__ == "__main__":
    unittest.main()
