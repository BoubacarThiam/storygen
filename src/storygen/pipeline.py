"""Orchestration des étapes médias (images, voix, sous-titres), partagée par
la CLI et l'interface web.

Les images (Pollinations, réseau) et la chaîne voix -> sous-titres (edge-tts
puis faster-whisper, réseau + CPU local) ne dépendent que de ``story.json`` :
elles tournent donc en parallèle. Le temps total devient celui de la plus
longue des deux branches, au lieu de leur somme — en pratique les images,
surtout sans clé Pollinations (1 image / 45 s).
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from storygen.models import Story
from storygen.steps.images import generate_images
from storygen.steps.subtitles import generate_subtitles
from storygen.steps.voice import generate_voices


def generate_media(
    story: Story,
    story_dir: Path,
    fmt: str,
    voice: str,
    force: bool = False,
    on_step: Callable[[str], None] | None = None,
) -> tuple[list[Path], dict[int, float], Path]:
    """Génère images, voix off et sous-titres. Retourne
    ``(chemins des images, {index: durée réelle}, chemin du SRT)``.

    ``on_step`` reçoit ``"images"`` au démarrage, puis ``"subtitles"`` si les
    images sont prêtes avant la chaîne audio (statut de la webapp)."""
    notify = on_step or (lambda _step: None)

    def _audio_chain() -> tuple[dict[int, float], Path]:
        durations = generate_voices(story, story_dir, voice=voice, force=force)
        srt_path = generate_subtitles(story, story_dir, durations, force=force)
        return durations, srt_path

    notify("images")
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="storygen-audio")
    audio_future = pool.submit(_audio_chain)
    try:
        image_paths = generate_images(story, story_dir, fmt=fmt, force=force)
    except BaseException:
        # Remonte l'échec tout de suite, sans attendre la fin de whisper.
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    if not audio_future.done():
        notify("subtitles")
    try:
        durations, srt_path = audio_future.result()
    finally:
        pool.shutdown(wait=True)

    return image_paths, durations, srt_path
