"""Tests de steps/script.py.

Deux catégories, clairement séparées :
- unitaires (aucun réseau) : extraction JSON, slugify ;
- réels contre le vrai endpoint public OpenRouter (aucune clé requise pour
  /models) : la découverte de modèles ':free'. Celui-ci est un vrai appel
  réseau, volontairement, pour ne jamais prétendre qu'un endpoint marche
  sans l'avoir vérifié.

generate_story() (l'appel de complétion complet) n'est PAS testé ici :
il requiert OPENROUTER_API_KEY, qui n'est pas encore configurée sur cette
machine. Voir README.md pour le test end-to-end à lancer une fois la clé
disponible.
"""

from __future__ import annotations

import unittest

from storygen.steps.script import _extract_json_block, discover_free_models
from storygen.utils.cache import slugify


class TestExtractJsonBlock(unittest.TestCase):
    def test_pure_json_passthrough(self) -> None:
        raw = '{"a": 1}'
        self.assertEqual(_extract_json_block(raw), '{"a": 1}')

    def test_strips_markdown_fence_with_json_tag(self) -> None:
        raw = '```json\n{"a": 1}\n```'
        self.assertEqual(_extract_json_block(raw).strip(), '{"a": 1}')

    def test_strips_markdown_fence_bare(self) -> None:
        raw = '```\n{"a": 1}\n```'
        self.assertEqual(_extract_json_block(raw).strip(), '{"a": 1}')

    def test_strips_surrounding_prose(self) -> None:
        raw = 'Voici le JSON demandé :\n{"a": 1}\nJ\'espère que ça convient !'
        self.assertEqual(_extract_json_block(raw), '{"a": 1}')


class TestSlugify(unittest.TestCase):
    def test_accents_and_spaces(self) -> None:
        self.assertEqual(
            slugify("Un pêcheur de Saint-Louis trouve une bouteille à la mer"),
            "un-pecheur-de-saint-louis-trouve-une-bouteille-a-la-mer",
        )

    def test_empty_falls_back(self) -> None:
        self.assertEqual(slugify("???"), "histoire")


class TestDiscoverFreeModelsLive(unittest.TestCase):
    """Vrai test réseau contre https://openrouter.ai/api/v1/models (public,
    aucune clé requise). Si ça échoue, c'est un vrai signal — pas un mock
    cassé."""

    def test_returns_nonempty_free_model_list(self) -> None:
        models = discover_free_models()
        self.assertIsInstance(models, list)
        self.assertGreater(len(models), 0)
        for model_id in models:
            self.assertTrue(model_id.endswith(":free"), model_id)


if __name__ == "__main__":
    unittest.main()
