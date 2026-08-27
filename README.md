# StoryGen

Pipeline **coût zéro** qui transforme un prompt texte en vidéo narrative
(scénario → images → voix off → sous-titres → montage), en français par
défaut, sortie 9:16 ou 16:9.

Aucun service payant : [OpenRouter](https://openrouter.ai/) (modèles `:free`),
[Pollinations.ai](https://pollinations.ai/) (images), `edge-tts` (voix),
`faster-whisper` en local (sous-titres), `ffmpeg` (montage).

## Statut actuel

- ✅ **Phase 1 — Scénarisation** : prompt → `story.json` structuré et validé.
- ✅ **Phase 2 — Images (Pollinations) + Voix (edge-tts)**
- ⏳ Phase 3 — Sous-titres (faster-whisper) + Montage (ffmpeg) → premier MP4
- ⏳ Phase 4 — Robustesse (reprise après échec, `--dry-run`, logs)

### Constats réels notables (voir aussi les commits)

- **Pollinations renvoie du JPEG, jamais du PNG** (`Content-Type: image/jpeg`, testé avec `&format=png` et `.png` dans l'URL — aucun effet). Les assets sont donc `scene_XX.jpg`, validés par signature JPEG + taille plancher, pas par signature PNG.
- **Pollinations (Cloudflare) renvoie 403 sur le User-Agent par défaut d'urllib** (`Python-urllib/3.x`) mais laisse passer une requête identifiée. `utils/http.py` envoie donc systématiquement `User-Agent: StoryGen/0.1 (...)`.
- Certains modèles OpenRouter `:free` haut de gamme (ex. `thinkingmachines/inkling*:free`) renvoient 403 hors "agentic harness" — géré par le repli en cascade.
- Un modèle `:free` a sous-respecté `--duration` (53s au lieu de 90s) jusqu'à ce que le prompt système impose un nombre de scènes suggéré + une tolérance explicite.

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

## Utilisation (Phases 1+2, pour l'instant)

```bash
uv run storygen "un pêcheur de Saint-Louis trouve une bouteille à la mer" --duration 90 --format 9:16
```

Produit `work/{slug}/story.json`, `work/{slug}/assets/img/scene_XX.jpg` et
`work/{slug}/assets/audio/scene_XX.mp3`. Relancer la même commande réutilise
le cache (pas de nouvel appel réseau) sauf avec `--force`. Le montage final
(Phase 3) n'est pas encore branché.

Chaque module est aussi appelable seul :

```bash
uv run python -m storygen.steps.script "un pêcheur..." --duration 90
uv run python -m storygen.steps.images work/{slug}/story.json --format 9:16
uv run python -m storygen.steps.voice work/{slug}/story.json --voice fr-FR-RemyMultilingualNeural
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
- `tests/test_images.py` — construction du prompt/URL (aucun réseau) **+**
  un vrai téléchargement Pollinations qui vérifie le contrat réel de
  l'endpoint (JPEG, User-Agent requis).
- `tests/test_voice.py` — vraie synthèse edge-tts (aucune clé requise) et
  vérification du cache disque.
- `tests/test_ffmpeg.py` — mesure réelle de durée via ffprobe sur un audio
  généré par ffmpeg (aucun réseau).

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
      images.py         # Phase 2 : images (Pollinations)
      voice.py            # Phase 2 : voix off (edge-tts)
      subtitles.py          # Phase 3 (à venir)
      render.py               # Phase 3 (à venir)
    utils/
      cache.py          # slug, cache disque
      http.py            # client HTTP stdlib (GET/POST JSON, download+retry, User-Agent)
      ffmpeg.py            # wrapper ffprobe (durée réelle des assets)
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
