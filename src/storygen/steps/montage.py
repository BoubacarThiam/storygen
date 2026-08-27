"""Étape 3b — Montage final : images + voix + sous-titres -> vidéo MP4
(ffmpeg, en local, gratuit).

Constat réel (fait écho à celui de steps/images.py) : Pollinations ignore
la résolution demandée dans l'URL et plafonne systématiquement à 1024 px
sur le plus grand côté, tout en respectant le ratio demandé (ex. reçu
576x1024 pour une demande 9:16, alors que la résolution de sortie visée
est 1080x1920). Chaque image est donc ré-agrandie avant le zoompan Ken
Burns plutôt que simplement recadrée — un léger lissage plutôt qu'une
perte de cadrage, le ratio étant déjà identique.

Pipeline, par scène : image statique zoomée/pannée (Ken Burns, zoompan)
pendant la durée réelle de l'audio de narration (celle mesurée par ffprobe
en Phase 2b, jamais ``duration_hint``) + audio muxé -> clip MP4. Les clips
sont concaténés bout à bout (mêmes paramètres d'encodage sur tous les
clips -> concat par flux, sans réencodage) puis les sous-titres (SRT de la
Phase 3a) sont incrustés ("burned-in", police système DejaVu Sans — aucun
fichier binaire supplémentaire à versionner).

Appelable seul :
    uv run python -m storygen.steps.montage work/{slug}/story.json --format 9:16
"""

from __future__ import annotations

import argparse
import logging
import tempfile
from pathlib import Path

from storygen import config
from storygen.models import Story
from storygen.steps.images import FORMAT_DIMENSIONS
from storygen.utils.cache import is_fresh
from storygen.utils.ffmpeg import FfmpegError, probe_duration, run_ffmpeg

logger = logging.getLogger("storygen.montage")

FPS = 25
ZOOM_MAX = 1.15  # zoom-in subtil et constant sur toute la durée du clip
# Pré-agrandissement avant zoompan : les images reçues (voir constat plus
# haut) sont plus petites que la résolution de sortie visée, et zoompan sur
# une image de petite taille tremble visiblement (quantification interne au
# pixel) — un facteur 2 avant le zoom/pan lisse largement ce défaut.
UPSCALE_FACTOR = 2
MIN_VIDEO_SIZE = 10_000


def _escape_filter_path(path: Path) -> str:
    """Échappe un chemin pour l'utiliser comme valeur d'option de filtre
    ffmpeg (``:`` sépare les options d'un filtre dans sa syntaxe)."""
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _build_scene_clip(
    image_path: Path, audio_path: Path, duration: float, width: int, height: int, out_path: Path
) -> None:
    """Image statique + audio de narration -> clip MP4 avec effet Ken Burns."""
    upscale_w, upscale_h = width * UPSCALE_FACTOR, height * UPSCALE_FACTOR
    frames = max(round(duration * FPS), 1)
    zoom_step = (ZOOM_MAX - 1.0) / frames
    filter_complex = (
        f"[0:v]scale={upscale_w}:{upscale_h}:flags=lanczos,"
        f"zoompan=z='min(zoom+{zoom_step:.8f},{ZOOM_MAX})':d={frames}:"
        "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={width}x{height}:fps={FPS}[v]"
    )
    run_ffmpeg([
        "-loop", "1", "-i", str(image_path),
        "-i", str(audio_path),
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-t", f"{duration:.3f}", "-shortest",
        str(out_path),
    ])


def _concat_clips(clip_paths: list[Path], out_path: Path, tmp_dir: Path) -> None:
    """Concatène des clips déjà encodés avec les mêmes paramètres, sans
    réencoder (``-c copy``) — cf. ``_build_scene_clip`` qui garantit ça."""
    list_file = tmp_dir / "concat_list.txt"
    list_file.write_text(
        "\n".join(f"file '{p.resolve()}'" for p in clip_paths), encoding="utf-8"
    )
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out_path)])


def _burn_subtitles(video_path: Path, srt_path: Path, out_path: Path) -> None:
    style = (
        f"FontName={config.SUBTITLE_FONT},Fontsize=20,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=80"
    )
    vf = f"subtitles={_escape_filter_path(srt_path)}:force_style='{style}'"
    run_ffmpeg([
        "-i", str(video_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        str(out_path),
    ])


def generate_montage(
    story: Story,
    story_dir: Path,
    durations: dict[int, float],
    srt_path: Path,
    fmt: str = config.DEFAULT_FORMAT,
    force: bool = False,
) -> Path:
    """Assemble images + voix + sous-titres en une vidéo MP4 finale, écrite
    dans ``output/{slug}.mp4``. Retourne le chemin du fichier produit."""
    if fmt not in FORMAT_DIMENSIONS:
        raise ValueError(f"Format inconnu : {fmt!r} (attendu : {list(FORMAT_DIMENSIONS)})")
    width, height = FORMAT_DIMENSIONS[fmt]

    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    final_path = config.OUTPUT_DIR / f"{story_dir.name}.mp4"

    if not force and is_fresh(final_path, min_size=MIN_VIDEO_SIZE):
        logger.info("Cache hit : %s", final_path)
        return final_path

    img_dir = story_dir / "assets" / "img"
    audio_dir = story_dir / "assets" / "audio"
    total = len(story.scenes)

    with tempfile.TemporaryDirectory(prefix="storygen_montage_") as tmp:
        tmp_dir = Path(tmp)
        clip_paths: list[Path] = []
        for i, scene in enumerate(story.scenes, start=1):
            image_path = img_dir / f"scene_{scene.index:02d}.jpg"
            audio_path = audio_dir / f"scene_{scene.index:02d}.mp3"
            if not image_path.is_file() or not audio_path.is_file():
                raise FileNotFoundError(
                    f"Assets manquants pour la scène {scene.index} (image ou audio) — "
                    "lance les Phases 2a/2b avant le montage."
                )
            duration = durations.get(scene.index)
            if duration is None:
                raise ValueError(f"Durée réelle manquante pour la scène {scene.index}.")

            clip_path = tmp_dir / f"scene_{scene.index:02d}.mp4"
            logger.info("Scène %d/%d : rendu Ken Burns (%.1fs)...", i, total, duration)
            _build_scene_clip(image_path, audio_path, duration, width, height, clip_path)
            clip_paths.append(clip_path)

        concat_path = tmp_dir / "concat.mp4"
        logger.info("Concaténation de %d clip(s)...", len(clip_paths))
        _concat_clips(clip_paths, concat_path, tmp_dir)

        logger.info("Incrustation des sous-titres...")
        try:
            _burn_subtitles(concat_path, srt_path, final_path)
        except FfmpegError:
            final_path.unlink(missing_ok=True)
            raise

    logger.info("-> %s (%.1f Mo)", final_path, final_path.stat().st_size / 1_048_576)
    return final_path


def _cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="StoryGen — Phase 3b : montage seul")
    parser.add_argument("story_path", type=Path, help="Chemin vers story.json")
    parser.add_argument("--format", choices=list(FORMAT_DIMENSIONS), default=config.DEFAULT_FORMAT)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    story = Story.model_validate_json(args.story_path.read_text(encoding="utf-8"))
    story_dir = args.story_path.parent
    audio_dir = story_dir / "assets" / "audio"
    durations = {
        scene.index: probe_duration(audio_dir / f"scene_{scene.index:02d}.mp3")
        for scene in story.scenes
    }
    srt_path = story_dir / "assets" / "subtitles.srt"
    if not srt_path.is_file():
        raise SystemExit(f"Sous-titres manquants : {srt_path} (lance la Phase 3a d'abord).")

    final_path = generate_montage(story, story_dir, durations, srt_path, fmt=args.format, force=args.force)
    print(f"\n✅ Vidéo finale : {final_path}")


if __name__ == "__main__":
    _cli()
