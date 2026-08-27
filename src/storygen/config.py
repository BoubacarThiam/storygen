"""Configuration du pipeline : chargement du ``.env`` et constantes.

Le chargement du ``.env`` est fait à la main (parseur minimal en stdlib) :
``python-dotenv`` n'est pas dans la stack imposée, et le format ``KEY=VALUE``
ne justifie pas une dépendance supplémentaire.
"""

from __future__ import annotations

import os
from pathlib import Path

# src/storygen/config.py -> src/storygen -> src -> racine du repo
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
WORK_DIR = PROJECT_ROOT / "work"
OUTPUT_DIR = PROJECT_ROOT / "output"

DEFAULT_VOICE = "fr-FR-RemyMultilingualNeural"
DEFAULT_DURATION_S = 90
DEFAULT_FORMAT = "9:16"
# Surchargeable via STORYGEN_WHISPER_MODEL : sur un hébergement gratuit à RAM
# limitée (ex. Render free, 512 Mo), "small" (~500 Mo) peut saturer la
# mémoire une fois ffmpeg lancé en parallèle — "base" (~150 Mo) ou "tiny"
# sont alors plus sûrs, au prix d'une transcription un peu moins précise.
WHISPER_MODEL_SIZE = os.environ.get("STORYGEN_WHISPER_MODEL", "small")

# Facteur de pré-agrandissement avant le Ken Burns (steps/montage.py) :
# plus il est élevé, plus le zoom/pan est fluide, mais plus ffmpeg consomme
# de RAM/CPU pendant l'encodage. 2 par défaut ; 1 sur un hébergement à RAM
# limitée (constaté : un OOM sur Render free avec whisper encore en
# mémoire + facteur 2 — voir aussi steps/subtitles.unload_model()).
MONTAGE_UPSCALE_FACTOR = int(os.environ.get("STORYGEN_MONTAGE_UPSCALE", "2"))

# Choix validé avec l'utilisateur : police système déjà présente, zéro
# fichier binaire supplémentaire à versionner.
SUBTITLE_FONT = "DejaVu Sans"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
POLLINATIONS_BASE_URL = "https://image.pollinations.ai/prompt"


def _load_dotenv(path: Path) -> None:
    """Charge un ``.env`` simple (une paire ``KEY=VALUE`` par ligne, ``#``
    pour les commentaires) dans os.environ, sans écraser une variable déjà
    présente dans l'environnement (permet de surcharger via le shell)."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_dotenv(PROJECT_ROOT / ".env")


def get_openrouter_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY manquant. Copie .env.example vers .env et "
            "renseigne ta clé gratuite (voir README.md, section OpenRouter)."
        )
    return key
