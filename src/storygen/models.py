"""Schémas Pydantic du contrat ``story.json``.

Ce module est la source de vérité du format d'échange entre l'étape de
scénarisation et toutes les étapes suivantes (images, voix, sous-titres,
montage). Voir le master prompt pour le contrat d'origine.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class StyleBible(BaseModel):
    """Décrit le style visuel unique appliqué à toutes les scènes."""

    art_style: str = Field(..., min_length=1)
    palette: str = Field(..., min_length=1)
    seed: int


class Character(BaseModel):
    """Un personnage récurrent. ``physical_description`` doit être stable :
    elle est concaténée telle quelle à chaque ``image_prompt`` où le
    personnage apparaît (voir steps/images.py, Phase 2)."""

    id: str = Field(..., min_length=1)
    physical_description: str = Field(..., min_length=1)


class Scene(BaseModel):
    """Une scène = un plan narré, une image, un extrait audio."""

    index: int = Field(..., ge=1)
    narration: str = Field(..., min_length=1)
    image_prompt: str = Field(..., min_length=1)
    characters_present: list[str] = Field(default_factory=list)
    duration_hint: float = Field(..., gt=0)


class Story(BaseModel):
    """Histoire complète, telle que produite par steps/script.py."""

    title: str = Field(..., min_length=1)
    logline: str = Field(..., min_length=1)
    style_bible: StyleBible
    characters: list[Character]
    scenes: list[Scene] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _check_references_and_order(self) -> "Story":
        known_ids = {c.id for c in self.characters}
        for scene in self.scenes:
            unknown = set(scene.characters_present) - known_ids
            if unknown:
                raise ValueError(
                    f"Scène {scene.index} référence des personnages "
                    f"inconnus de 'characters' : {sorted(unknown)}"
                )

        indices = [s.index for s in self.scenes]
        if indices != sorted(indices):
            raise ValueError("Les scènes doivent être triées par 'index' croissant.")
        if len(set(indices)) != len(indices):
            raise ValueError("Deux scènes partagent le même 'index'.")

        return self

    @property
    def total_duration_hint(self) -> float:
        return sum(s.duration_hint for s in self.scenes)
