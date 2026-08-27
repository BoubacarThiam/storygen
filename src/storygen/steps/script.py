"""Étape 1 — Scénarisation : prompt libre -> ``story.json`` structuré et validé.

Appelable seul :
    uv run python -m storygen.steps.script "un pêcheur de Saint-Louis..." --duration 90

Stratégie de repli (validée avec l'utilisateur) : plutôt qu'une liste de
modèles codée en dur, on interroge au runtime l'endpoint public
``/models`` d'OpenRouter (aucune clé requise pour lister), on filtre les
identifiants ``:free`` et on les essaie dans l'ordre jusqu'à succès. Ça
reste résilient si un modèle disparaît ou est saturé.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from pydantic import ValidationError

from storygen import config
from storygen.models import Story
from storygen.utils.cache import is_fresh, slugify
from storygen.utils.http import HttpError, get_json, post_json

logger = logging.getLogger("storygen.script")

CHAT_ENDPOINT = f"{config.OPENROUTER_BASE_URL}/chat/completions"
MODELS_ENDPOINT = f"{config.OPENROUTER_BASE_URL}/models"

# Modèles ":free" à écarter même s'ils matchent le suffixe : ce ne sont pas
# des modèles conversationnels (embeddings, modération, reranking...).
_MODEL_BLOCKLIST_HINTS = ("embed", "guard", "moderation", "rerank")


def discover_free_models(timeout: float = 15.0) -> list[str]:
    """Retourne les modèles ``:free`` d'OpenRouter, triés par fenêtre de
    contexte décroissante (proxy simple pour "modèle plus capable en
    premier"). Lève une erreur explicite si l'endpoint ne répond pas."""
    data = get_json(MODELS_ENDPOINT, timeout=timeout)
    models = data.get("data", [])
    free = [
        m
        for m in models
        if m.get("id", "").endswith(":free")
        and not any(hint in m["id"].lower() for hint in _MODEL_BLOCKLIST_HINTS)
    ]
    free.sort(key=lambda m: m.get("context_length") or 0, reverse=True)
    ids = [m["id"] for m in free]
    if not ids:
        raise RuntimeError("Aucun modèle ':free' disponible sur OpenRouter actuellement.")
    return ids


def build_system_prompt(duration_s: int) -> str:
    # Estimation grossière : ~20 mots/scène à un débit oral naturel (~2.8 mots/s)
    # -> ~7s/scène. Donne au modèle un nombre de scènes concret à viser plutôt
    # qu'une contrainte de durée abstraite qu'il a tendance à sous-estimer.
    avg_scene_s = 7.0
    suggested_scenes = max(3, round(duration_s / avg_scene_s))
    tolerance_s = max(10, round(duration_s * 0.15))

    return f"""Tu es un scénariste qui écrit des histoires courtes en français, \
structurées en scènes, pour une vidéo narrée verticale (type YouTube Shorts / TikTok).

RÈGLES DE SORTIE — CRITIQUES :
- Réponds UNIQUEMENT avec du JSON pur, strictement valide (RFC 8259).
- Aucun texte avant ou après le JSON. Aucun bloc markdown (pas de ```). Aucun commentaire.
- Le JSON doit respecter EXACTEMENT ce schéma :

{{
  "title": string,
  "logline": string,
  "style_bible": {{
    "art_style": string (style visuel, EN ANGLAIS, ex: "warm watercolor illustration, soft cinematic lighting"),
    "palette": string (couleurs dominantes, EN ANGLAIS),
    "seed": integer (entier fixe, choisi une fois, réutilisé pour toutes les scènes)
  }},
  "characters": [
    {{ "id": string (slug court en minuscules, ex: "pecheur"), "physical_description": string (EN ANGLAIS, précis, réutilisable tel quel dans un prompt d'image) }}
  ],
  "scenes": [
    {{
      "index": integer (commence à 1, continu),
      "narration": string (texte lu par la voix off, EN FRANÇAIS, entre 15 et 25 mots),
      "image_prompt": string (EN ANGLAIS, décrit UNIQUEMENT l'action/le cadre de cette scène : ne redécris ni les personnages ni le style, ils seront ajoutés automatiquement),
      "characters_present": [string] (ids de "characters" présents dans cette scène, [] si aucun),
      "duration_hint": number (secondes, estimation réaliste de la lecture de "narration" à un débit naturel)
    }}
  ]
}}

CONTRAINTES DE CONTENU :
- Écris environ {suggested_scenes} scènes pour cette histoire (ni beaucoup moins, ni beaucoup plus).
- IMPORTANT : la somme de tous les "duration_hint" DOIT être comprise entre \
{duration_s - tolerance_s} et {duration_s + tolerance_s} secondes (cible : {duration_s}s). \
Si tu as prévu moins de scènes que nécessaire pour l'atteindre, ajoutes-en plutôt que de \
gonfler artificiellement la durée d'une scène.
- Chaque personnage listé dans "characters" a une "physical_description" STABLE : \
ne décris jamais un même personnage différemment d'une scène à l'autre.
- Histoire complète avec un début, un développement et une fin claire, écrite pour \
être racontée à voix haute (phrases courtes, orales, immersives)."""


def _extract_json_block(text: str) -> str:
    """Tolère un modèle qui aurait quand même entouré sa réponse de ``` ou
    de texte parasite avant/après : isole le plus grand bloc {...}."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return text
    return text[start : end + 1]


def _call_model(model: str, system_prompt: str, user_prompt: str, api_key: str) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.9,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = post_json(CHAT_ENDPOINT, payload, headers=headers, timeout=90.0)
    try:
        return resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise HttpError(f"Réponse OpenRouter inattendue : {json.dumps(resp)[:500]}") from e


def generate_story(
    prompt: str,
    duration_s: int = config.DEFAULT_DURATION_S,
    force: bool = False,
    work_dir: Path | None = None,
) -> tuple[Story, Path]:
    """Orchestration complète de la Phase 1. Retourne (Story validée, dossier
    work/{slug}/). Cache : si un story.json valide existe déjà, il est
    réutilisé sans appel réseau, sauf ``force=True``."""
    slug = slugify(prompt)
    story_dir = (work_dir or config.WORK_DIR) / slug
    story_dir.mkdir(parents=True, exist_ok=True)
    story_path = story_dir / "story.json"

    if not force and is_fresh(story_path):
        try:
            story = Story.model_validate_json(story_path.read_text(encoding="utf-8"))
            logger.info("Cache hit : %s (utilise --force pour régénérer)", story_path)
            return story, story_dir
        except (ValidationError, json.JSONDecodeError) as e:
            logger.warning("story.json en cache invalide (%s) — régénération.", e)

    api_key = config.get_openrouter_api_key()
    system_prompt = build_system_prompt(duration_s)
    user_prompt = f"Sujet de l'histoire : {prompt}"

    models = discover_free_models()
    preview = ", ".join(models[:5]) + ("…" if len(models) > 5 else "")
    logger.info("Modèles ':free' disponibles (ordre d'essai) : %s", preview)

    last_error: Exception | None = None
    for model in models:
        for attempt in range(2):  # 1 essai + 1 relance automatique sur JSON invalide
            try:
                raw = _call_model(model, system_prompt, user_prompt, api_key)
            except HttpError as e:
                logger.warning("Modèle %s indisponible (%s) — passage au suivant.", model, e)
                last_error = e
                break  # erreur réseau/HTTP : ne consomme pas la relance JSON

            json_text = _extract_json_block(raw)
            try:
                data = json.loads(json_text)
                story = Story.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as e:
                last_error = e
                if attempt == 0:
                    logger.warning("JSON invalide depuis %s (%s) — une relance automatique.", model, e)
                    continue
                logger.warning("JSON toujours invalide depuis %s après relance — modèle suivant.", model)
                break

            story_path.write_text(story.model_dump_json(indent=2), encoding="utf-8")
            logger.info("story.json généré via %s -> %s", model, story_path)
            return story, story_dir

    raise RuntimeError(
        f"Échec de scénarisation sur {len(models)} modèle(s) ':free' testés. "
        f"Dernière erreur : {last_error}"
    )


def _cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="StoryGen — Phase 1 : scénarisation seule")
    parser.add_argument("prompt")
    parser.add_argument("--duration", type=int, default=config.DEFAULT_DURATION_S)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    story, story_dir = generate_story(args.prompt, duration_s=args.duration, force=args.force)
    print(json.dumps(story.model_dump(), indent=2, ensure_ascii=False))
    print(f"\n-> {story_dir / 'story.json'}", flush=True)


if __name__ == "__main__":
    _cli()
