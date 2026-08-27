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
- ✅ **Phase 3 — Sous-titres (faster-whisper) + Montage (ffmpeg) → premier MP4**
- ✅ **Interface web** : formulaire en ligne (`webapp.py`) + déploiement Docker/Render (voir plus bas).
- ⏳ Phase 4 — Robustesse (reprise après échec, `--dry-run`, logs)

### Constats réels notables (voir aussi les commits)

- **Pollinations renvoie du JPEG, jamais du PNG** (`Content-Type: image/jpeg`, testé avec `&format=png` et `.png` dans l'URL — aucun effet). Les assets sont donc `scene_XX.jpg`, validés par signature JPEG + taille plancher, pas par signature PNG.
- **Pollinations (Cloudflare) renvoie 403 sur le User-Agent par défaut d'urllib** (`Python-urllib/3.x`) mais laisse passer une requête identifiée. `utils/http.py` envoie donc systématiquement `User-Agent: StoryGen/0.1 (...)`.
- Certains modèles OpenRouter `:free` haut de gamme (ex. `thinkingmachines/inkling*:free`) renvoient 403 hors "agentic harness" — géré par le repli en cascade.
- Un modèle `:free` a sous-respecté `--duration` (53s au lieu de 90s) jusqu'à ce que le prompt système impose un nombre de scènes suggéré + une tolérance explicite.
- **Pollinations ignore aussi la résolution demandée** (`width`/`height` dans l'URL) et plafonne systématiquement à 1024 px sur le plus grand côté (ex. `576x1024` reçu pour une demande 9:16), mais respecte le ratio. `steps/montage.py` ré-agrandit donc chaque image avant le Ken Burns plutôt que de compter sur la résolution demandée.
- **`zoompan` tremble visiblement sur une image de petite taille** (quantification interne au pixel) : un pré-agrandissement ×2 avant le zoom/pan (filtre `scale`, lanczos) lisse largement l'effet.
- **Le modèle faster-whisper `small` (~500 Mo) se télécharge depuis Hugging Face au premier lancement** (aucune clé requise, mais peut prendre plusieurs minutes selon la connexion) — normal une seule fois, mis en cache ensuite dans `~/.cache/huggingface`.
- Limite connue (candidate Phase 4) : un segment whisper correspondant à une phrase longue peut se retrouver découpé en 4-5 lignes de sous-titres affichées en même temps — lisible, mais pas idéal visuellement. Un vrai découpage par groupes de mots avec timing (`word_timestamps=True`) donnerait un rendu plus proche des standards (2 lignes max, incrustées progressivement).

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

## Utilisation

```bash
uv run storygen "un pêcheur de Saint-Louis trouve une bouteille à la mer" --duration 90 --format 9:16
```

Produit `work/{slug}/story.json`, `work/{slug}/assets/img/scene_XX.jpg`,
`work/{slug}/assets/audio/scene_XX.mp3`, `work/{slug}/assets/subtitles.srt`
et la vidéo finale `output/{slug}.mp4`. Relancer la même commande réutilise
le cache (pas de nouvel appel réseau ni réencodage) sauf avec `--force`.

Chaque module est aussi appelable seul :

```bash
uv run python -m storygen.steps.script "un pêcheur..." --duration 90
uv run python -m storygen.steps.images work/{slug}/story.json --format 9:16
uv run python -m storygen.steps.voice work/{slug}/story.json --voice fr-FR-RemyMultilingualNeural
uv run python -m storygen.steps.subtitles work/{slug}/story.json
uv run python -m storygen.steps.montage work/{slug}/story.json --format 9:16
```

## Interface web

```bash
uv run uvicorn storygen.webapp:app --reload
# -> http://127.0.0.1:8000
```

Formulaire (prompt, durée, format, voix) → génération en tâche de fond →
lien vers le MP4 une fois prêt. Compte 1 à 5 minutes par vidéo en local ;
plus lent sur un hébergement gratuit à faible CPU (voir Déploiement).

### Déploiement (Render, gratuit)

```bash
git push   # le repo doit être sur GitHub
```

1. Sur [render.com](https://render.com/) : **New** → **Blueprint** → sélectionne ce repo (`render.yaml` est déjà présent).
2. Une fois le service créé, renseigne `OPENROUTER_API_KEY` dans l'onglet **Environment** (volontairement absente du repo — voir `sync: false` dans `render.yaml`).
3. Le premier déploiement construit l'image Docker (`ffmpeg` + police + modèle faster-whisper `tiny` pré-téléchargés dans l'image, voir `Dockerfile`) — plusieurs minutes.

Limites réelles assumées du plan gratuit (voir aussi `webapp.py`) :

- **CPU/RAM limités** (0.1 CPU, 512 Mo) : whisper + ffmpeg y sont nettement plus lents qu'en local — une vidéo de 60s peut prendre 15-20 minutes au lieu de 3-4.
- **OOM constaté en conditions réelles, à deux endroits différents** : d'abord le modèle whisper `base` (~150 Mo) plantait (502, job perdu) pendant la transcription elle-même ; une fois passé sur `tiny` (~75 Mo) + modèle libéré avant le montage (`steps/subtitles.unload_model()`), le plantage s'est déplacé sur **l'encodage ffmpeg lui-même** en pleine résolution (1080x1920). Trois réglages ont été nécessaires ensemble pour tenir dans 512 Mo :
  - `STORYGEN_WHISPER_MODEL=tiny` (au lieu de `small` en local)
  - `STORYGEN_MONTAGE_UPSCALE=1` (pas de pré-agrandissement avant le Ken Burns)
  - `STORYGEN_OUTPUT_SCALE=0.5` (vidéo finale en 540x960 plutôt que 1080x1920 — proche de la résolution native reçue de Pollinations, donc sans perte de netteté visible) + `STORYGEN_FFMPEG_THREADS=2` (évite que libx264 sur-alloue des buffers par thread sur un CPU partagé)
- **Mise en veille après 15 min d'inactivité**, réveil ~30-60s au prochain visiteur. La page interroge `/status` toutes les 4s pendant une génération, ce qui maintient le service actif tant qu'un onglet reste ouvert.
- **Disque éphémère** : les jobs (en mémoire) et les vidéos déjà générées sont perdus au redémarrage du service (déploiement, ou crash) — télécharge le MP4 dès qu'il est prêt. Un vrai usage multi-utilisateurs voudrait une file de jobs (Redis/RQ) et un stockage objet persistant (S3/R2) à la place.
- Si les plantages persistent malgré tout ça, l'étape suivante réaliste est de passer sur un plan payant (plus de RAM) plutôt que de continuer à dégrader la qualité — 512 Mo est une contrainte dure pour whisper + ffmpeg + Python ensemble.

Tester l'image Docker en local avant de déployer :

```bash
docker build -t storygen .
docker run -p 8000:8000 --env-file .env -e PORT=8000 storygen
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
- `tests/test_ffmpeg.py` — mesure réelle de durée via ffprobe et exécution
  réelle de `run_ffmpeg` (succès et échec), sur des fichiers générés par
  ffmpeg (aucun réseau).
- `tests/test_subtitles.py` — logique de formatage/découpage (aucun réseau)
  **+** une vraie transcription faster-whisper d'un MP3 edge-tts réel
  (télécharge le modèle `small` au premier lancement, voir plus haut).
- `tests/test_montage.py` — vrai montage ffmpeg (Ken Burns + concat +
  sous-titres incrustés) sur des assets de test générés localement (aucun
  réseau), vérifie la durée finale et le cache.
- `tests/test_webapp.py` — routage/validation réels (aucun réseau) sur le
  serveur FastAPI ; l'orchestration des jobs remplace les 5 étapes du
  pipeline (déjà couvertes par de vrais tests ci-dessus) par des doubles
  rapides pour vérifier les transitions de statut sans attendre plusieurs
  minutes à chaque run de la suite.

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
    webapp.py          # interface web (uvicorn storygen.webapp:app)
    config.py         # .env, constantes
    models.py         # schémas Pydantic (contrat story.json)
    templates/
      index.html         # formulaire + polling JS de l'interface web
    steps/
      script.py        # Phase 1 : scénarisation
      images.py         # Phase 2 : images (Pollinations)
      voice.py            # Phase 2 : voix off (edge-tts)
      subtitles.py          # Phase 3a : sous-titres (faster-whisper)
      montage.py               # Phase 3b : montage final (ffmpeg, Ken Burns)
    utils/
      cache.py          # slug, cache disque
      http.py            # client HTTP stdlib (GET/POST JSON, download+retry, User-Agent)
      ffmpeg.py            # wrapper ffmpeg/ffprobe (durée réelle, exécution encodage)
  work/{slug}/         # assets intermédiaires d'une histoire (ignoré par git)
  output/              # MP4 finaux (ignoré par git)
  Dockerfile           # image de déploiement (ffmpeg + police + modèle whisper pré-téléchargés)
  render.yaml          # blueprint Render (plan gratuit)
  tests/
```

## Contrat `story.json`

Voir [`src/storygen/models.py`](src/storygen/models.py) — c'est la source de
vérité. Point clé pour la cohérence visuelle : `style_bible.art_style` et la
`physical_description` de chaque personnage présent seront automatiquement
concaténés à chaque `image_prompt` avant l'appel à Pollinations (Phase 2).
Le LLM ne doit jamais redécrire un personnage différemment d'une scène à
l'autre — c'est imposé dans le prompt système de `steps/script.py`.
