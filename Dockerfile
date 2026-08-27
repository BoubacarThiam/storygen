# Image de déploiement de StoryGen (interface web, voir src/storygen/webapp.py).
#
# ffmpeg (montage), fonts-dejavu-core (sous-titres incrustés — la police
# SUBTITLE_FONT choisie dans config.py) et le modèle faster-whisper sont
# tous nécessaires à l'exécution ; les deux premiers via apt, le dernier
# est pré-téléchargé ici (build) plutôt qu'au premier lancement (runtime) :
# sur un hébergement à disque éphémère, un téléchargement au runtime serait
# refait à chaque redémarrage — lent et fragile (voir README).
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-dejavu-core \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.6 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# --no-dev : httpx (tests uniquement) n'a pas sa place dans l'image de prod.
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

# "tiny" (~75 Mo) plutôt que "small" (~500 Mo, défaut local) : constaté en
# conditions réelles sur Render free (512 Mo) — "base" (~150 Mo) suffisait
# encore à faire planter (OOM) le conteneur pendant la transcription elle-
# même, même après libération explicite du modèle avant le montage (voir
# steps/subtitles.unload_model()). "tiny" transcrit moins bien mais tient
# dans la mémoire disponible. Surchargeable au déploiement via --build-arg.
ARG STORYGEN_WHISPER_MODEL=tiny
ENV STORYGEN_WHISPER_MODEL=${STORYGEN_WHISPER_MODEL}
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('${STORYGEN_WHISPER_MODEL}', device='cpu', compute_type='int8')"

EXPOSE 8000
CMD ["sh", "-c", "uvicorn storygen.webapp:app --host 0.0.0.0 --port ${PORT:-8000}"]
