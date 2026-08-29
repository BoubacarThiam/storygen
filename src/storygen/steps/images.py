"""Étape 2a — Images : story.json -> assets/img/scene_XX.jpg (Pollinations.ai).

Appelable seul :
    uv run python -m storygen.steps.images work/{slug}/story.json --format 9:16

Cohérence visuelle : le style_bible.art_style, la palette, et la
physical_description de chaque personnage présent dans la scène sont
automatiquement concaténés à image_prompt avant l'appel à Pollinations — le
LLM n'a jamais à redécrire un personnage.

Constat réel important (signalé à l'utilisateur) : contrairement à ce que
son nom laisse penser, l'endpoint Pollinations renvoie du JPEG
(Content-Type: image/jpeg), jamais du PNG, quels que soient les paramètres
essayés. Le pipeline est adapté à ce contrat réel plutôt qu'à celui supposé.
"""

from __future__ import annotations

import argparse
import logging
import time
import urllib.parse
from pathlib import Path

from storygen import config
from storygen.models import Scene, Story
from storygen.utils.cache import is_fresh
from storygen.utils.http import HttpError, download_binary

logger = logging.getLogger("storygen.images")

FORMAT_DIMENSIONS = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
}

# Marge appliquée à la résolution demandée, pour laisser de la place au
# Ken Burns (zoompan, Phase 3) sans jamais montrer de bord noir.
KEN_BURNS_MARGIN = 1.15

# Ne pas marteler un service gratuit : pause entre deux appels séquentiels.
# Constat réel : un usage soutenu (nombreuses générations rapprochées) fait
# apparaître des 429 (Too Many Requests) — 1.5s ne suffisait pas toujours à
# les éviter, 2.5s réduit nettement leur fréquence sans trop allonger le
# temps total (voir aussi utils/http._backoff_seconds pour le retry).
PAUSE_BETWEEN_CALLS_S = 2.5

JPEG_MAGIC = b"\xff\xd8\xff"
MIN_IMAGE_SIZE = 3000  # heuristique : une image d'erreur/placeholder est presque toujours plus petite

_IMAGE_PROMPT_MAX_LEN = 800  # garde-fou pour la longueur d'URL une fois encodée


def _dimensions_for_format(fmt: str) -> tuple[int, int]:
    if fmt not in FORMAT_DIMENSIONS:
        raise ValueError(f"Format inconnu : {fmt!r} (attendu : {list(FORMAT_DIMENSIONS)})")
    base_w, base_h = FORMAT_DIMENSIONS[fmt]
    w = round(base_w * KEN_BURNS_MARGIN / 2) * 2
    h = round(base_h * KEN_BURNS_MARGIN / 2) * 2
    return w, h


def build_full_image_prompt(story: Story, scene: Scene, max_len: int = _IMAGE_PROMPT_MAX_LEN) -> str:
    """Concatène : action de la scène + descriptions des personnages présents
    + style visuel global. C'est le cœur de la cohérence visuelle."""
    char_map = {c.id: c for c in story.characters}
    parts = [scene.image_prompt]
    for char_id in scene.characters_present:
        char = char_map.get(char_id)
        if char is not None:
            parts.append(char.physical_description)
    parts.append(story.style_bible.art_style)
    parts.append(story.style_bible.palette)

    prompt = ", ".join(parts)
    if len(prompt) > max_len:
        logger.warning(
            "Prompt image scène %d tronqué (%d -> %d caractères) pour rester dans une URL raisonnable.",
            scene.index, len(prompt), max_len,
        )
        prompt = prompt[:max_len]
    return prompt


def build_pollinations_url(prompt: str, seed: int, width: int, height: int, model: str = "flux") -> str:
    encoded = urllib.parse.quote(prompt, safe="")
    return (
        f"{config.POLLINATIONS_BASE_URL}/{encoded}"
        f"?width={width}&height={height}&model={model}&nologo=true&seed={seed}"
    )


def _is_valid_image(data: bytes) -> bool:
    return len(data) >= MIN_IMAGE_SIZE and data[:3] == JPEG_MAGIC


def generate_images(
    story: Story,
    story_dir: Path,
    fmt: str = config.DEFAULT_FORMAT,
    force: bool = False,
) -> list[Path]:
    """Génère (ou réutilise depuis le cache) une image par scène. Retourne
    les chemins dans l'ordre des scènes."""
    img_dir = story_dir / "assets" / "img"
    img_dir.mkdir(parents=True, exist_ok=True)
    width, height = _dimensions_for_format(fmt)

    paths: list[Path] = []
    total = len(story.scenes)
    for i, scene in enumerate(story.scenes):
        target = img_dir / f"scene_{scene.index:02d}.jpg"

        if not force and is_fresh(target, min_size=MIN_IMAGE_SIZE):
            logger.info("Cache hit : %s", target)
            paths.append(target)
            continue

        prompt = build_full_image_prompt(story, scene)
        url = build_pollinations_url(prompt, story.style_bible.seed, width, height)
        logger.info("Scène %d/%d : génération image (%dx%d)...", scene.index, total, width, height)

        try:
            # retries=5 plutôt que 3 : un 429 attend maintenant 10s/20s/30s/40s
            # entre les tentatives (voir utils/http._backoff_seconds), donc plus
            # d'essais donne une vraie chance de voir la limite se lever.
            data = download_binary(url, timeout=60.0, retries=5, validate=_is_valid_image)
        except HttpError as e:
            raise RuntimeError(
                f"Échec définitif de génération d'image pour la scène {scene.index} : {e}"
            ) from e

        target.write_bytes(data)
        logger.info("-> %s (%.0f Ko)", target, len(data) / 1024)
        paths.append(target)

        if i < total - 1:
            time.sleep(PAUSE_BETWEEN_CALLS_S)

    return paths


def _cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="StoryGen — Phase 2a : images seules")
    parser.add_argument("story_path", type=Path, help="Chemin vers story.json")
    parser.add_argument("--format", choices=list(FORMAT_DIMENSIONS), default=config.DEFAULT_FORMAT)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    story = Story.model_validate_json(args.story_path.read_text(encoding="utf-8"))
    paths = generate_images(story, args.story_path.parent, fmt=args.format, force=args.force)
    print(f"\n✅ {len(paths)} image(s) générée(s)/réutilisée(s) dans {args.story_path.parent / 'assets' / 'img'}")


if __name__ == "__main__":
    _cli()
