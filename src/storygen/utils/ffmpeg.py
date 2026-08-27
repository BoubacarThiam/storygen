"""Wrappers autour de ffmpeg/ffprobe (déjà présents sur le système imposé par
la stack, aucune dépendance Python supplémentaire)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


class FfmpegError(RuntimeError):
    """Erreur normalisée pour tout appel à ffmpeg/ffprobe."""


def probe_duration(path: Path) -> float:
    """Durée réelle d'un fichier audio/vidéo en secondes, via ffprobe.

    Utilisé par steps/voice.py : la durée réelle du MP3 fait autorité sur
    ``duration_hint`` pour le montage (Phase 3), comme précisé dans le
    master prompt.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-print_format", "json", "-show_format",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        raise FfmpegError(f"ffprobe a échoué sur {path} : {e}") from e

    try:
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        raise FfmpegError(f"Impossible de lire la durée de {path} : {e}") from e


def run_ffmpeg(args: list[str], timeout: float = 300.0) -> None:
    """Exécute ``ffmpeg -y -v error <args>`` et lève ``FfmpegError`` (avec le
    stderr complet) en cas d'échec. Utilisé par steps/montage.py pour les
    clips Ken Burns, la concaténation et l'incrustation des sous-titres."""
    cmd = ["ffmpeg", "-y", "-v", "error", *args]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        raise FfmpegError(f"ffmpeg n'a pas pu s'exécuter ({' '.join(cmd)}) : {e}") from e
    if result.returncode != 0:
        raise FfmpegError(f"ffmpeg a échoué (code {result.returncode}) : {result.stderr.strip()}")
