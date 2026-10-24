"""Schémas Pydantic du contrat ``story.json``.

Ce module est la source de vérité du format d'échange entre l'étape de
scénarisation et toutes les étapes suivantes (images, voix, sous-titres,
montage). Voir le master prompt pour le contrat d'origine.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

# Constat réel : Pollinations renvoie un 500 (pas une erreur propre) dès que
# ``seed`` dépasse l'intervalle d'un entier signé 32 bits — probablement un
# int32 quelque part dans leur pipeline. Le prompt système de script.py ne
# donne aucune borne au LLM, qui a produit un seed de 10 chiffres
# (3081976424) faisant échouer toute la génération d'images de l'histoire.
SEED_MAX = 2_147_483_647  # 2**31 - 1


class StyleBible(BaseModel):
    """Décrit le style visuel unique appliqué à toutes les scènes."""

    art_style: str = Field(..., min_length=1)
    palette: str = Field(..., min_length=1)
    seed: int

    @field_validator("seed")
    @classmethod
    def _clamp_seed(cls, v: int) -> int:
        """Ramène tout entier (y compris négatif ou hors int32) dans
        l'intervalle accepté par Pollinations, plutôt que de faire échouer
        toute la génération d'images pour un seed que le LLM a choisi trop
        grand (voir SEED_MAX)."""
        return abs(v) % (SEED_MAX + 1)


class Character(BaseModel):
    """Un personnage récurrent. ``physical_description`` doit être stable :
    elle est concaténée telle quelle à chaque ``image_prompt`` où le
    personnage apparaît (voir steps/images.py, Phase 2)."""

    id: str = Field(..., min_length=1)
    physical_description: str = Field(..., min_length=1)


class Scene(BaseModel):
    """Une scène = un plan narré, une image, un extrait audio.

    ``motion_prompt`` est optionnel (rétro-compatible avec les anciens
    ``story.json``) : il décrit la même scène un instant plus tard, avec un
    mouvement visible. ``steps/images.py`` génère alors une seconde image
    et ``steps/montage.py`` fond de l'une à l'autre — c'est ce qui donne un
    vrai mouvement de contenu plutôt qu'un simple zoom sur une photo figée.
    """

    index: int = Field(..., ge=1)
    narration: str = Field(..., min_length=1)
    image_prompt: str = Field(..., min_length=1)
    motion_prompt: str | None = Field(default=None)
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
