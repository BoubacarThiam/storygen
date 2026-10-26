"""Étape 2a — Images : story.json -> assets/img/scene_XX.jpg (Pollinations.ai).

Appelable seul :
    uv run python -m storygen.steps.images work/{slug}/story.json --format 9:16

Cohérence visuelle : le style_bible.art_style, la palette, et la
physical_description de chaque personnage présent dans la scène sont
automatiquement concaténés à image_prompt avant l'appel à Pollinations — le
LLM n'a jamais à redécrire un personnage.

Animation : pour chaque scène, une seconde image « un instant plus tard »
est générée (``scene_XX_motion.jpg``) à partir de ``motion_prompt``. Le
montage fond de l'une à l'autre pour donner un vrai mouvement de contenu,
plutôt qu'un simple zoom Ken Burns sur une photo figée. Les deux images
sont générées en parallèle (``STORYGEN_IMAGE_CONCURRENCY``) pour rester
rapide malgré le double d'appels — uniquement quand une clé Pollinations est
configurée.

Sans clé, Pollinations bascule sur l'API Classic anonyme : une seule image
à la fois, au moins 45 s d'intervalle par IP. La génération est alors
volontairement séquentielle et espacée (sinon 402/503) ; c'est lent mais
fonctionnel. Avec ``STORYGEN_POLLINATIONS_API_KEY``, l'endpoint authentifié
``gen.pollinations.ai`` est utilisé et la concurrence normale s'applique.

Constat réel important (signalé à l'utilisateur) : contrairement à ce que
son nom laisse penser, l'endpoint Pollinations renvoie du JPEG
(Content-Type: image/jpeg), jamais du PNG, quels que soient les paramètres
essayés. Le pipeline est adapté à ce contrat réel plutôt qu'à celui supposé.
"""

from __future__ import annotations

import argparse
import logging
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from storygen import config
from storygen.models import SEED_MAX, Scene, Story
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

JPEG_MAGIC = b"\xff\xd8\xff"
MIN_IMAGE_SIZE = 3000  # heuristique : une image d'erreur/placeholder est presque toujours plus petite

_IMAGE_PROMPT_MAX_LEN = 800  # garde-fou pour la longueur d'URL une fois encodée

# Ajouté automatiquement à image_prompt quand le LLM n'a pas fourni de
# motion_prompt (anciens story.json) : garantit que chaque scène peut tout
# de même être animée.
MOTION_PROMPT_SUFFIX = (
    "the same scene a moment later, visible natural motion, dynamic movement, "
    "cinematic motion blur, same framing and composition"
)


def _dimensions_for_format(fmt: str) -> tuple[int, int]:
    if fmt not in FORMAT_DIMENSIONS:
        raise ValueError(f"Format inconnu : {fmt!r} (attendu : {list(FORMAT_DIMENSIONS)})")
    base_w, base_h = FORMAT_DIMENSIONS[fmt]
    w = round(base_w * KEN_BURNS_MARGIN / 2) * 2
    h = round(base_h * KEN_BURNS_MARGIN / 2) * 2
    return w, h


def build_full_image_prompt(
    story: Story, scene: Scene, max_len: int = _IMAGE_PROMPT_MAX_LEN, motion: bool = False
) -> str:
    """Concatène : action de la scène + descriptions des personnages présents
    + style visuel global. C'est le cœur de la cohérence visuelle.

    Avec ``motion=True``, on utilise ``scene.motion_prompt`` (ou, à défaut,
    ``image_prompt`` suffixé d'une indication de mouvement) pour produire la
    seconde image-clé de la scène."""
    char_map = {c.id: c for c in story.characters}
    if motion:
        scene_part = scene.motion_prompt or f"{scene.image_prompt}, {MOTION_PROMPT_SUFFIX}"
    else:
        scene_part = scene.image_prompt

    parts = [scene_part]
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
    base = config.POLLINATIONS_BASE_URL.rstrip("/")
    # gen.pollinations.ai/image attend le prompt dans l'URL path.
    # image.pollinations.ai/prompt fait de même.
    # La clé API n'est JAMAIS mise dans l'URL : elle finirait dans les logs et
    # les messages d'erreur. Elle voyage dans l'en-tête Authorization
    # (voir _download_one).
    return (
        f"{base}/{encoded}"
        f"?width={width}&height={height}&model={model}&nologo=true&seed={seed}"
    )


def _is_anonymous_pollinations() -> bool:
    """Vrai quand on utilise l'API Classic publique (sans clé), soumise à la
    limite d'une image toutes les 45 s par IP."""
    return (
        not config.POLLINATIONS_API_KEY
        and "image.pollinations.ai" in config.POLLINATIONS_BASE_URL
    )


# Verrou global : la limite Classic est par IP, donc elle s'applique à tous les
# threads/jobs du process, pas seulement à une scène. On sérialise les
# téléchargements anonymes et on respecte l'intervalle entre deux requêtes.
_anonymous_gate = threading.Lock()
_last_anonymous_download_finished_at = 0.0


def _is_valid_image(data: bytes) -> bool:
    return len(data) >= MIN_IMAGE_SIZE and data[:3] == JPEG_MAGIC


def _download_one(
    url: str,
    target: Path,
    scene_index: int,
    label: str,
    min_interval_s: float = 0.0,
) -> None:
    """Télécharge une image et l'écrit sur disque (exécuté dans un thread)."""
    global _last_anonymous_download_finished_at
    logger.info("Scène %d : génération %s...", scene_index, label)

    headers: dict[str, str] = {}
    if config.POLLINATIONS_API_KEY:
        headers["Authorization"] = f"Bearer {config.POLLINATIONS_API_KEY}"

    def _fetch() -> bytes:
        # retries=5 : un 402/429/503 attend 45s/60s entre les tentatives (voir
        # utils/http._backoff_seconds), donc plus d'essais donne une vraie
        # chance de voir la limite se lever.
        return download_binary(
            url, timeout=90.0, retries=5, validate=_is_valid_image, headers=headers
        )

    if min_interval_s > 0:
        with _anonymous_gate:
            wait = _last_anonymous_download_finished_at + min_interval_s - time.monotonic()
            if wait > 0:
                logger.info(
                    "Scène %d : limite Pollinations anonyme — attente de %.0fs "
                    "avant la prochaine image...",
                    scene_index, wait,
                )
                time.sleep(wait)
            try:
                data = _fetch()
            finally:
                _last_anonymous_download_finished_at = time.monotonic()
    else:
        data = _fetch()

    target.write_bytes(data)
    logger.info("-> %s (%.0f Ko)", target, len(data) / 1024)


def generate_images(
    story: Story,
    story_dir: Path,
    fmt: str = config.DEFAULT_FORMAT,
    force: bool = False,
) -> list[Path]:
    """Génère (ou réutilise depuis le cache) les images de chaque scène.
    Retourne les chemins des images principales, dans l'ordre des scènes.

    Les images de mouvement (``scene_XX_motion.jpg``) sont générées en plus
    quand ``config.ANIMATE_SCENES`` est actif. Un échec sur une image de
    mouvement n'est pas fatal : la scène retombe simplement sur le zoom
    Ken Burns statique au montage."""
    img_dir = story_dir / "assets" / "img"
    img_dir.mkdir(parents=True, exist_ok=True)
    width, height = _dimensions_for_format(fmt)
    seed = story.style_bible.seed
    motion_seed = (seed + 1) % (SEED_MAX + 1)
    total = len(story.scenes)

    # (url, cible, index de scène, libellé, obligatoire)
    jobs: list[tuple[str, Path, int, str, bool]] = []
    primary_paths: list[Path] = []

    for scene in story.scenes:
        primary = img_dir / f"scene_{scene.index:02d}.jpg"
        primary_paths.append(primary)
        if not force and is_fresh(primary, min_size=MIN_IMAGE_SIZE):
            logger.info("Cache hit : %s", primary)
        else:
            prompt = build_full_image_prompt(story, scene)
            url = build_pollinations_url(prompt, seed, width, height)
            jobs.append((url, primary, scene.index, "image", True))

        if config.ANIMATE_SCENES:
            motion = img_dir / f"scene_{scene.index:02d}_motion.jpg"
            if not force and is_fresh(motion, min_size=MIN_IMAGE_SIZE):
                logger.info("Cache hit : %s", motion)
            else:
                prompt = build_full_image_prompt(story, scene, motion=True)
                url = build_pollinations_url(prompt, motion_seed, width, height)
                jobs.append((url, motion, scene.index, "image de mouvement", False))

    if jobs:
        anonymous = _is_anonymous_pollinations()
        min_interval = config.POLLINATIONS_ANONYMOUS_MIN_INTERVAL_S if anonymous else 0.0
        # API Classic anonyme : une seule requête à la fois. Avec une clé,
        # l'endpoint gen.pollinations.ai supporte la concurrence configurée.
        workers = min(1 if anonymous else config.IMAGE_CONCURRENCY, len(jobs))
        logger.info(
            "%d image(s) à générer pour %d scène(s) (concurrence : %d)...",
            len(jobs), total, workers,
        )
        if anonymous:
            logger.warning(
                "Aucune clé Pollinations (STORYGEN_POLLINATIONS_API_KEY) : API "
                "Classic anonyme, une image toutes les %.0fs minimum. La "
                "génération sera lente ; une clé avec du Pollen gratuit "
                "(Quests) sur https://enter.pollinations.ai/keys supprime "
                "cette limite.",
                min_interval,
            )
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="storygen-img") as pool:
            future_jobs = {}
            for url, target, scene_index, label, required in jobs:
                future = pool.submit(
                    _download_one, url, target, scene_index, label, min_interval
                )
                future_jobs[future] = (scene_index, label, required)
                # Décalage entre soumissions : évite les rafales de 429.
                if config.IMAGE_STAGGER_S > 0:
                    time.sleep(config.IMAGE_STAGGER_S)

            for future in as_completed(future_jobs):
                scene_index, label, required = future_jobs[future]
                try:
                    future.result()
                except HttpError as e:
                    if required:
                        for pending in future_jobs:
                            pending.cancel()
                        raise RuntimeError(
                            f"Échec définitif de génération d'image pour la scène "
                            f"{scene_index} : {e}"
                        ) from e
                    logger.warning(
                        "Scène %d : %s indisponible (%s) — la scène utilisera le "
                        "mouvement Ken Burns statique.", scene_index, label, e,
                    )

    return primary_paths


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
