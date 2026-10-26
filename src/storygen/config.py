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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("", "0", "false", "no", "off")


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


# Chargé AVANT les constantes ci-dessous : les surcharges locales
# (STORYGEN_ANIMATE, STORYGEN_MONTAGE_CRF, etc.) sont ainsi prises en compte
# depuis .env, pas seulement depuis l'environnement du shell.
_load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_VOICE = "fr-FR-RemyMultilingualNeural"

# Sélection courte de voix edge-tts françaises pour le menu déroulant de
# l'interface web (webapp.py) — édge-tts en propose des dizaines, la
# plupart inutiles ici ; ces 4 couvrent homme/femme et le nécessaire.
FRENCH_VOICES = [
    ("fr-FR-RemyMultilingualNeural", "Rémy (homme)"),
    ("fr-FR-HenriNeural", "Henri (homme)"),
    ("fr-FR-DeniseNeural", "Denise (femme)"),
    ("fr-FR-VivienneMultilingualNeural", "Vivienne (femme)"),
]
DEFAULT_DURATION_S = 90
DEFAULT_FORMAT = "9:16"
# Surchargeable via STORYGEN_WHISPER_MODEL : sur un hébergement gratuit à RAM
# limitée (ex. Render free, 512 Mo), "small" (~500 Mo) peut saturer la
# mémoire une fois ffmpeg lancé en parallèle — "base" (~150 Mo) ou "tiny"
# sont alors plus sûrs, au prix d'une transcription un peu moins précise.
WHISPER_MODEL_SIZE = os.environ.get("STORYGEN_WHISPER_MODEL", "small")

# ---------------------------------------------------------------------------
# Images (steps/images.py)
# ---------------------------------------------------------------------------

# Nombre de téléchargements Pollinations simultanés. La génération d'images
# était le principal goulot d'étranglement (appels strictement séquentiels +
# pause de 2,5 s) : quelques requêtes en parallèle divisent le temps total
# sans marteler le service gratuit. Mettre 1 pour revenir au séquentiel.
IMAGE_CONCURRENCY = max(1, int(os.environ.get("STORYGEN_IMAGE_CONCURRENCY", "3")))

# Décalage entre deux soumissions de requêtes (secondes) : évite d'envoyer
# une rafale simultanée à Pollinations, qui répond alors 429. Le backoff de
# utils/http.py prend le relais si malgré tout la limite est atteinte.
IMAGE_STAGGER_S = max(0.0, float(os.environ.get("STORYGEN_IMAGE_STAGGER", "0.6")))

# Anime chaque scène avec deux images-clés (départ / un instant plus tard)
# fondues par ffmpeg. C'est ce qui donne un vrai mouvement de contenu, au
# lieu du simple zoom Ken Burns sur une photo figée. Mettre 0 pour revenir
# à des images fixes (utile sur un hébergement très limité en CPU/RAM).
ANIMATE_SCENES = _env_bool("STORYGEN_ANIMATE", True)

# ---------------------------------------------------------------------------
# Montage (steps/montage.py)
# ---------------------------------------------------------------------------

# Facteur de pré-agrandissement avant le Ken Burns : plus il est élevé,
# plus le zoom/pan est fluide, mais plus ffmpeg consomme de RAM/CPU pendant
# l'encodage. 2 par défaut ; 1 sur un hébergement à RAM limitée (constaté :
# un OOM sur Render free avec whisper encore en mémoire + facteur 2 — voir
# aussi steps/subtitles.unload_model()).
MONTAGE_UPSCALE_FACTOR = int(os.environ.get("STORYGEN_MONTAGE_UPSCALE", "2"))

# Facteur d'échelle appliqué à la résolution de sortie (steps/montage.py) :
# 1.0 = résolution native du format (1080x1920 / 1920x1080). Constaté en
# conditions réelles sur Render free (512 Mo) : même après tiny + upscale
# x1, libx264 en pleine résolution suffit encore à faire planter (OOM) le
# conteneur pendant l'encodage. Réduire cette échelle diminue d'autant la
# mémoire nécessaire (proportionnelle au nombre de pixels).
MONTAGE_OUTPUT_SCALE = float(os.environ.get("STORYGEN_OUTPUT_SCALE", "1.0"))

# Qualité d'encodage libx264 (steps/montage.py). Le défaut de libx264 (23)
# vise une qualité quasi sans perte. Pour ce contenu (images générées +
# mouvement doux), CRF 20 donne des dégradés plus propres et des détails
# plus nets que l'ancien CRF 30, sans exploser le poids. Montez la valeur
# (23-26) pour des fichiers plus légers, descendez (18) pour une qualité
# maximale.
MONTAGE_CRF = int(os.environ.get("STORYGEN_MONTAGE_CRF", "20"))

# Fréquence d'image de sortie. 30 fps rend le fondu/mouvement nettement
# plus fluide que 25 fps, pour un surcoût d'encodage modéré.
MONTAGE_FPS = int(os.environ.get("STORYGEN_MONTAGE_FPS", "30"))

# Preset libx264 : "veryfast" garde la génération rapide ; "fast"/"medium"
# améliorent légèrement la compression au prix du temps de calcul.
MONTAGE_PRESET = os.environ.get("STORYGEN_MONTAGE_PRESET", "veryfast")

# Débit audio AAC de la vidéo finale (la narration gagne en clarté).
MONTAGE_AUDIO_BITRATE = os.environ.get("STORYGEN_MONTAGE_AUDIO_BITRATE", "128k")

# Threads ffmpeg pour l'encodage (steps/montage.py). ``0`` = auto-détection
# par ffmpeg, qui peut sur-allouer des buffers par thread sur un conteneur
# où plusieurs CPU logiques sont visibles mais où le quota réel est très
# inférieur (ex. Render free, "0.1 CPU") — un cap explicite réduit la
# mémoire de pointe sans changer la qualité de sortie.
MONTAGE_FFMPEG_THREADS = int(os.environ.get("STORYGEN_FFMPEG_THREADS", "0"))

# Choix validé avec l'utilisateur : police système déjà présente, zéro
# fichier binaire supplémentaire à versionner.
SUBTITLE_FONT = "DejaVu Sans"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Pollinations : l'endpoint historique image.pollinations.ai/prompt devient
# de plus en plus souvent restreint (402). On passe par gen.pollinations.ai/image
# par défaut. Surchargeable via STORYGEN_POLLINATIONS_BASE_URL / STORYGEN_POLLINATIONS_API_KEY.
POLLINATIONS_BASE_URL = os.environ.get(
    "STORYGEN_POLLINATIONS_BASE_URL", "https://gen.pollinations.ai/image"
)
POLLINATIONS_API_KEY = os.environ.get("STORYGEN_POLLINATIONS_API_KEY", "").strip()


def get_openrouter_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY manquant. Copie .env.example vers .env et "
            "renseigne ta clé gratuite (voir README.md, section OpenRouter)."
        )
    return key
