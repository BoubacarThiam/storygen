"""Étape 2b — Voix off : story.json -> assets/audio/scene_XX.mp3 (edge-tts).

Appelable seul :
    uv run python -m storygen.steps.voice work/{slug}/story.json --voice fr-FR-RemyMultilingualNeural

La durée réelle du MP3 (mesurée via ffprobe) fait autorité sur
``duration_hint`` pour le montage (Phase 3) — cf. master prompt.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import edge_tts

from storygen import config
from storygen.models import Story
from storygen.utils.cache import is_fresh
from storygen.utils.ffmpeg import probe_duration

logger = logging.getLogger("storygen.voice")

# Ne pas ouvrir trop de connexions simultanées vers le service gratuit.
MAX_CONCURRENCY = 4
RETRIES = 3
MIN_MP3_SIZE = 500  # heuristique : même une narration très courte dépasse largement ça

# Écart entre durée réelle et duration_hint au-delà duquel on avertit
# (purement informatif : c'est la durée réelle qui fera foi au montage).
DURATION_DRIFT_WARN_S = 3.0


async def _synthesize_one(narration: str, voice: str, target: Path, scene_index: int, semaphore: asyncio.Semaphore) -> None:
    async with semaphore:
        last_error: Exception | None = None
        for attempt in range(RETRIES):
            try:
                communicate = edge_tts.Communicate(narration, voice)
                await communicate.save(str(target))
                return
            except Exception as e:  # edge-tts (non officiel) peut lever divers types d'erreurs réseau
                last_error = e
                if attempt < RETRIES - 1:
                    logger.warning(
                        "edge-tts a échoué pour la scène %d (%s) — tentative %d/%d...",
                        scene_index, e, attempt + 2, RETRIES,
                    )
                    await asyncio.sleep(2**attempt)
        raise RuntimeError(
            f"Échec définitif edge-tts pour la scène {scene_index} après {RETRIES} tentatives : {last_error}"
        )


async def _generate_voices_async(
    story: Story, audio_dir: Path, voice: str, force: bool
) -> dict[int, float]:
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    targets: dict[int, Path] = {}
    tasks = []

    for scene in story.scenes:
        target = audio_dir / f"scene_{scene.index:02d}.mp3"
        targets[scene.index] = target
        if not force and is_fresh(target, min_size=MIN_MP3_SIZE):
            logger.info("Cache hit : %s", target)
            continue
        tasks.append(_synthesize_one(scene.narration, voice, target, scene.index, semaphore))

    if tasks:
        await asyncio.gather(*tasks)

    durations: dict[int, float] = {}
    for scene in story.scenes:
        target = targets[scene.index]
        real = probe_duration(target)
        durations[scene.index] = real
        drift = abs(real - scene.duration_hint)
        if drift > DURATION_DRIFT_WARN_S:
            logger.warning(
                "Scène %d : durée réelle du MP3 = %.1fs vs duration_hint = %.1fs (écart %.1fs) — "
                "la durée réelle fera autorité au montage.",
                scene.index, real, scene.duration_hint, drift,
            )
    return durations


def generate_voices(
    story: Story,
    story_dir: Path,
    voice: str = config.DEFAULT_VOICE,
    force: bool = False,
) -> dict[int, float]:
    """Génère (ou réutilise) un MP3 par scène. Retourne {index: durée réelle en s}."""
    audio_dir = story_dir / "assets" / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    return asyncio.run(_generate_voices_async(story, audio_dir, voice, force))


def _cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="StoryGen — Phase 2b : voix off seule")
    parser.add_argument("story_path", type=Path, help="Chemin vers story.json")
    parser.add_argument("--voice", default=config.DEFAULT_VOICE)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    story = Story.model_validate_json(args.story_path.read_text(encoding="utf-8"))
    durations = generate_voices(story, args.story_path.parent, voice=args.voice, force=args.force)
    total = sum(durations.values())
    print(f"\n✅ {len(durations)} piste(s) audio générée(s)/réutilisée(s), durée totale réelle : {total:.1f}s")


if __name__ == "__main__":
    _cli()
