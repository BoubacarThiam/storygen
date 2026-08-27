"""Utilitaires de cache disque partagés par toutes les étapes.

Principe : chaque histoire vit dans ``work/{slug}/``. Chaque étape régénère
un asset seulement s'il est absent ou invalide — jamais si un run précédent
l'a déjà produit correctement. Voir ``steps/script.py`` pour un exemple
d'utilisation (``is_fresh`` + ``--force``).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path


def slugify(text: str, max_len: int = 60) -> str:
    """Transforme un prompt libre en nom de dossier stable et lisible.

    Ex: "Un pêcheur de Saint-Louis..." -> "un-pecheur-de-saint-louis"
    """
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    slug = slug[:max_len].rstrip("-")
    return slug or "histoire"


def content_hash(*parts: str) -> str:
    """Empreinte courte et stable d'un ensemble de paramètres (prompt, seed,
    modèle...). Sert à invalider un cache quand les entrées changent, sans
    dépendre uniquement du nom de fichier."""
    h = hashlib.sha256("\x1f".join(parts).encode("utf-8"))
    return h.hexdigest()[:12]


def is_fresh(path: Path, min_size: int = 1) -> bool:
    """Un asset est considéré valide s'il existe et dépasse une taille
    plancher — évite de faire confiance à un fichier vide ou tronqué suite
    à un échec réseau en cours d'écriture."""
    try:
        return path.is_file() and path.stat().st_size >= min_size
    except OSError:
        return False
