"""Étape 3b — Montage final : images + voix + sous-titres -> vidéo MP4
(ffmpeg, en local, gratuit).

Deux améliorations structurantes par rapport à la première version :

1. **Mouvement réel** — quand ``assets/img/scene_XX_motion.jpg`` existe
   (générée depuis ``motion_prompt``, voir steps/images.py), la scène n'est
   plus une seule photo zoomée : deux images-clés (départ / un instant plus
   tard) sont fondues l'une dans l'autre, avec une caméra qui zoome et
   panote. Le contenu bouge donc réellement (personnage, eau, fumée...), au
   lieu d'un simple effet Ken Burns sur une image figée.
2. **Un seul encodage** — les sous-titres sont incrustés directement dans
   chaque clip de scène (SRT découpé par scène), puis les clips sont
   concaténés avec ``-c copy``. L'ancienne version réencodait toute la
   vidéo une seconde fois pour les sous-titres : on supprime donc une
   passe d'encodage complète (plus rapide, et pas de perte de qualité
   cumulée).

Appelable seul :
    uv run python -m storygen.steps.montage work/{slug}/story.json --format 9:16
"""

from __future__ import annotations

import argparse
import logging
import re
import tempfile
from pathlib import Path

from storygen import config
from storygen.models import Story
from storygen.steps.images import FORMAT_DIMENSIONS, MIN_IMAGE_SIZE
from storygen.utils.cache import is_fresh
from storygen.utils.ffmpeg import FfmpegError, probe_duration, run_ffmpeg

logger = logging.getLogger("storygen.montage")

FPS = config.MONTAGE_FPS
ZOOM_MAX = 1.15  # amplitude du zoom Ken Burns
# Pré-agrandissement avant zoompan : les images reçues (voir constat dans
# steps/images.py) sont plus petites que la résolution de sortie visée, et
# zoompan sur une image de petite taille tremble visiblement. Un facteur 2
# avant le zoom/pan lisse largement ce défaut (surchargeable via
# STORYGEN_MONTAGE_UPSCALE ; 1 sur un hébergement à RAM limitée).
UPSCALE_FACTOR = config.MONTAGE_UPSCALE_FACTOR
MIN_VIDEO_SIZE = 10_000

# Répartition du fondu entre les deux images d'une scène animée : l'image de
# départ est tenue ~25% du temps, le fondu dure ~50%, puis l'image d'arrivée
# est tenue ~25%. Un fondu sur toute la durée donnerait une image fantôme
# peu lisible ; ce rythme laisse le temps de lire chaque état.
MOTION_HOLD_RATIO = 0.25
MOTION_FADE_RATIO = 0.50
MOTION_MIN_DURATION_S = 0.6

_SRT_TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


def _escape_filter_path(path: Path) -> str:
    """Échappe un chemin pour l'utiliser comme valeur d'option de filtre
    ffmpeg (``:`` sépare les options d'un filtre dans sa syntaxe). Les
    antislashs sont normalisés en ``/`` pour rester valides sous Windows
    comme sous Linux."""
    value = str(path).replace("\\", "/")
    return value.replace(":", "\\:").replace("'", "\\'")


def _threads_args() -> list[str]:
    threads = config.MONTAGE_FFMPEG_THREADS
    return ["-threads", str(threads)] if threads > 0 else []


def _format_srt_timestamp(seconds: float) -> str:
    total_ms = round(max(seconds, 0.0) * 1000)
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    secs, ms = divmod(rem_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _parse_srt(text: str) -> list[tuple[float, float, str]]:
    """Parse un SRT en ``[(début, fin, texte)]`` (timestamps en secondes)."""
    entries: list[tuple[float, float, str]] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.splitlines()
        if len(lines) < 3:
            continue
        match = _SRT_TIME_RE.search("\n".join(lines[:2]))
        if match is None:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(g) for g in match.groups())
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
        end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
        content = "\n".join(lines[2:]).strip()
        if content:
            entries.append((start, end, content))
    return entries


def _scene_entries(
    entries: list[tuple[float, float, str]], offset: float, duration: float
) -> list[tuple[float, float, str]]:
    """Découpe les sous-titres globaux pour ne garder que ceux de la scène,
    en ramenant leurs timestamps au début du clip (0 = début de la scène)."""
    scene_end = offset + duration
    local: list[tuple[float, float, str]] = []
    for start, end, text in entries:
        if end <= offset or start >= scene_end:
            continue
        local_start = max(start, offset) - offset
        local_end = min(end, scene_end) - offset
        if local_end - local_start >= 0.05:
            local.append((local_start, local_end, text))
    return local


def _write_scene_srt(entries: list[tuple[float, float, str]], path: Path) -> bool:
    if not entries:
        return False
    blocks = [
        f"{i}\n{_format_srt_timestamp(start)} --> {_format_srt_timestamp(end)}\n{text}\n"
        for i, (start, end, text) in enumerate(entries, start=1)
    ]
    path.write_text("\n".join(blocks), encoding="utf-8")
    return True


def _camera_filter(scene_index: int, frames: int, width: int, height: int) -> str:
    """Zoompan avec un mouvement de caméra varié (zoom avant/arrière +
    panoramique) plutôt qu'un zoom centré identique sur toutes les scènes."""
    step = (ZOOM_MAX - 1.0) / frames
    if scene_index % 2 == 1:
        zoom_expr = f"min(1+{step:.8f}*on,{ZOOM_MAX})"
    else:
        zoom_expr = f"max({ZOOM_MAX}-{step:.8f}*on,1.0)"

    progress = f"on/{frames}"
    mode = scene_index % 3
    if mode == 0:
        x_expr = f"(iw-iw/zoom)*{progress}"
        y_expr = "ih/2-(ih/zoom/2)"
    elif mode == 1:
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = f"(ih-ih/zoom)*{progress}"
    else:
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"

    return (
        f"zoompan=z='{zoom_expr}':d={frames}:x='{x_expr}':y='{y_expr}':"
        f"s={width}x{height}:fps={FPS}"
    )


def _subtitle_filter(srt_path: Path, ref_width: int, ref_height: int) -> str:
    # original_size fixe la résolution de référence utilisée pour interpréter
    # Fontsize : sans lui, ffmpeg prend une référence interne minuscule et la
    # police devient disproportionnée dès qu'on change STORYGEN_OUTPUT_SCALE.
    style = (
        f"FontName={config.SUBTITLE_FONT},Fontsize=10,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=80"
    )
    return (
        f"subtitles={_escape_filter_path(srt_path)}:"
        f"original_size={ref_width}x{ref_height}:force_style='{style}'"
    )


def _build_scene_clip(
    image_path: Path,
    audio_path: Path,
    duration: float,
    width: int,
    height: int,
    ref_width: int,
    ref_height: int,
    scene_index: int,
    out_path: Path,
    motion_path: Path | None = None,
    subtitle_path: Path | None = None,
) -> None:
    """Une scène -> un clip MP4 : deux images fondues (si disponibles) ou
    zoom Ken Burns, sous-titres incrustés, audio muxé."""
    upscale_w, upscale_h = width * UPSCALE_FACTOR, height * UPSCALE_FACTOR
    frames = max(round(duration * FPS), 1)
    camera = _camera_filter(scene_index, frames, width, height)
    base = f"scale={upscale_w}:{upscale_h}:flags=lanczos,setsar=1,{camera}"

    # Léger renforcement de netteté (compense l'agrandissement d'une source
    # plus petite) + micro-étalonnage pour des couleurs un peu plus riches.
    post = "unsharp=5:5:0.8:5:5:0.0,eq=contrast=1.03:saturation=1.05"
    if subtitle_path is not None:
        post += "," + _subtitle_filter(subtitle_path, ref_width, ref_height)
    post += ",format=yuv420p"

    animated = (
        motion_path is not None
        and config.ANIMATE_SCENES
        and duration >= MOTION_MIN_DURATION_S
    )
    if animated:
        offset = duration * MOTION_HOLD_RATIO
        fade = min(duration * MOTION_FADE_RATIO, duration - offset - 0.05)
        filter_complex = (
            f"[0:v]{base}[a];"
            f"[1:v]{base}[b];"
            f"[a][b]xfade=transition=fade:duration={fade:.3f}:offset={offset:.3f},"
            f"{post}[v]"
        )
        inputs = ["-i", str(image_path), "-i", str(motion_path), "-i", str(audio_path)]
        audio_map = "2:a"
        logger.info(
            "Scène %d : mouvement réel (%.1fs, fondu %.1fs)...", scene_index, duration, fade
        )
    else:
        filter_complex = f"[0:v]{base},{post}[v]"
        inputs = ["-i", str(image_path), "-i", str(audio_path)]
        audio_map = "1:a"
        logger.info("Scène %d : Ken Burns (%.1fs)...", scene_index, duration)

    run_ffmpeg([
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", audio_map,
        "-c:v", "libx264", "-preset", config.MONTAGE_PRESET, "-crf", str(config.MONTAGE_CRF),
        "-r", str(FPS), "-pix_fmt", "yuv420p",
        *_threads_args(),
        "-c:a", "aac", "-b:a", config.MONTAGE_AUDIO_BITRATE,
        "-t", f"{duration:.3f}", "-shortest",
        str(out_path),
    ])


def _concat_clips(clip_paths: list[Path], out_path: Path, tmp_dir: Path) -> None:
    """Concatène des clips déjà encodés avec les mêmes paramètres, sans
    réencoder (``-c copy``) — cf. ``_build_scene_clip`` qui garantit ça."""
    list_file = tmp_dir / "concat_list.txt"
    list_file.write_text(
        "\n".join(f"file '{str(p.resolve()).replace(chr(92), '/')}'" for p in clip_paths),
        encoding="utf-8",
    )
    run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", "-movflags", "+faststart", str(out_path),
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
    base_width, base_height = FORMAT_DIMENSIONS[fmt]
    scale = config.MONTAGE_OUTPUT_SCALE
    width = round(base_width * scale / 2) * 2
    height = round(base_height * scale / 2) * 2

    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    final_path = config.OUTPUT_DIR / f"{story_dir.name}.mp4"

    if not force and is_fresh(final_path, min_size=MIN_VIDEO_SIZE):
        logger.info("Cache hit : %s", final_path)
        return final_path

    img_dir = story_dir / "assets" / "img"
    audio_dir = story_dir / "assets" / "audio"
    total = len(story.scenes)
    srt_entries = _parse_srt(srt_path.read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory(prefix="storygen_montage_") as tmp:
        tmp_dir = Path(tmp)
        clip_paths: list[Path] = []
        offset = 0.0
        for i, scene in enumerate(story.scenes, start=1):
            image_path = img_dir / f"scene_{scene.index:02d}.jpg"
            audio_path = audio_dir / f"scene_{scene.index:02d}.mp3"
            motion_path = img_dir / f"scene_{scene.index:02d}_motion.jpg"
            if not image_path.is_file() or not audio_path.is_file():
                raise FileNotFoundError(
                    f"Assets manquants pour la scène {scene.index} (image ou audio) — "
                    "lance les Phases 2a/2b avant le montage."
                )
            duration = durations.get(scene.index)
            if duration is None:
                raise ValueError(f"Durée réelle manquante pour la scène {scene.index}.")

            motion_for_scene: Path | None = (
                motion_path if is_fresh(motion_path, min_size=MIN_IMAGE_SIZE) else None
            )

            scene_srt = tmp_dir / f"scene_{scene.index:02d}.srt"
            has_subs = _write_scene_srt(
                _scene_entries(srt_entries, offset, duration), scene_srt
            )

            clip_path = tmp_dir / f"scene_{scene.index:02d}.mp4"
            logger.info("Scène %d/%d : rendu (%.1fs)...", i, total, duration)
            _build_scene_clip(
                image_path=image_path,
                audio_path=audio_path,
                duration=duration,
                width=width,
                height=height,
                ref_width=base_width,
                ref_height=base_height,
                scene_index=scene.index,
                out_path=clip_path,
                motion_path=motion_for_scene,
                subtitle_path=scene_srt if has_subs else None,
            )
            clip_paths.append(clip_path)
            offset += duration

        logger.info("Concaténation de %d clip(s) (sans réencodage)...", len(clip_paths))
        try:
            _concat_clips(clip_paths, final_path, tmp_dir)
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
