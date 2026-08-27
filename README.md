# StoryGen

Pipeline **coût zéro** qui transforme un prompt texte en vidéo narrative
(scénario → images → voix off → sous-titres → montage), en français par
défaut, sortie 9:16 ou 16:9.

Aucun service payant : [OpenRouter](https://openrouter.ai/) (modèles `:free`),
[Pollinations.ai](https://pollinations.ai/) (images), `edge-tts` (voix),
`faster-whisper` en local (sous-titres), `ffmpeg` (montage).

## Statut actuel

- ✅ **Phase 1 — Scénarisation** : prompt → `story.json` structuré et validé.
- ⏳ Phase 2 — Images (Pollinations) + Voix (edge-tts)
- ⏳ Phase 3 — Sous-titres (faster-whisper) + Montage (ffmpeg) → premier MP4
- ⏳ Phase 4 — Robustesse (reprise après échec, `--dry-run`, logs)

## Installation

```bash
uv sync
cp .env.example .env
# puis édite .env et colle ta clé OpenRouter (gratuite, voir ci-dessous)
```

### Obtenir une clé OpenRouter (gratuite, sans carte bancaire)

1. Crée un compte sur https://openrouter.ai/
2. Va dans **Keys** (https://openrouter.ai/keys) → **Create Key**
3. Colle la clé dans `.env` : `OPENROUTER_API_KEY=sk-or-...`

Les modèles utilisés se terminent par `:free` — quota limité mais aucun
paiement possible par erreur. StoryGen découvre automatiquement la liste des
modèles `:free` disponibles au runtime et bascule sur le suivant en cas
d'indisponibilité.

## Utilisation (Phase 1 seule, pour l'instant)

```bash
uv run storygen "un pêcheur de Saint-Louis trouve une bouteille à la mer" --duration 90
```

Produit `work/{slug}/story.json`. Relancer la même commande réutilise le
cache (pas de nouvel appel réseau) sauf avec `--force`.

Le module est aussi appelable seul :

```bash
uv run python -m storygen.steps.script "un pêcheur..." --duration 90
```

## Tests

```bash
uv run python -m unittest discover -s tests -v
```

- `tests/test_models.py` — schéma Pydantic (aucun réseau).
- `tests/test_script.py` — extraction JSON, slugify (aucun réseau) **+**
  un vrai test réseau contre `GET /api/v1/models` d'OpenRouter (endpoint
  public, aucune clé requise) pour vérifier que la découverte de modèles
  `:free` fonctionne réellement.

Le test end-to-end complet de la génération (`generate_story`, appel de
complétion réel) nécessite `OPENROUTER_API_KEY` dans `.env` — à lancer
manuellement une fois la clé configurée :

```bash
uv run storygen "un pêcheur de Saint-Louis trouve une bouteille à la mer"
```

## Structure du projet

```
storygen/
  src/storygen/
    cli.py            # point d'entrée (uv run storygen ...)
    config.py         # .env, constantes
    models.py         # schémas Pydantic (contrat story.json)
    steps/
      script.py        # Phase 1 : scénarisation
      images.py         # Phase 2 (à venir)
      voice.py           # Phase 2 (à venir)
      subtitles.py        # Phase 3 (à venir)
      render.py             # Phase 3 (à venir)
    utils/
      cache.py          # slug, cache disque
      http.py            # client HTTP stdlib (GET/POST JSON, download+retry)
  work/{slug}/         # assets intermédiaires d'une histoire (ignoré par git)
  output/              # MP4 finaux (ignoré par git)
  tests/
```

## Contrat `story.json`

Voir [`src/storygen/models.py`](src/storygen/models.py) — c'est la source de
vérité. Point clé pour la cohérence visuelle : `style_bible.art_style` et la
`physical_description` de chaque personnage présent seront automatiquement
concaténés à chaque `image_prompt` avant l'appel à Pollinations (Phase 2).
Le LLM ne doit jamais redécrire un personnage différemment d'une scène à
l'autre — c'est imposé dans le prompt système de `steps/script.py`.
