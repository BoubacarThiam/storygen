"""Serveur web de StoryGen — interface en ligne pour générer une vidéo
depuis un navigateur, sans passer par la CLI.

Lancement local :
    uv run uvicorn storygen.webapp:app --reload
En déploiement (Dockerfile) :
    uvicorn storygen.webapp:app --host 0.0.0.0 --port $PORT

Limite assumée (hébergement gratuit à disque éphémère, ex. Render free) :
les jobs vivent en mémoire dans un seul process, et les vidéos sur le
disque local du conteneur (``config.OUTPUT_DIR``). Un redémarrage du
service (déploiement, ou simple mise en veille par inactivité) perd les
jobs en cours et les vidéos déjà générées mais pas encore téléchargées.
Acceptable pour un usage personnel à faible volume ; un vrai déploiement
multi-utilisateurs voudrait une file de jobs (ex. Redis/RQ) et un stockage
objet persistant (ex. Cloudflare R2, S3) à la place.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from storygen import config
from storygen.steps.images import FORMAT_DIMENSIONS, generate_images
from storygen.steps.montage import generate_montage
from storygen.steps.script import generate_story
from storygen.steps.subtitles import generate_subtitles, unload_model as unload_whisper_model
from storygen.steps.voice import generate_voices

logger = logging.getLogger("storygen.webapp")

JobStatus = Literal["queued", "script", "images", "voice", "subtitles", "montage", "done", "error"]

STEP_LABELS: dict[JobStatus, str] = {
    "queued": "En attente...",
    "script": "Scénarisation en cours...",
    "images": "Génération des images...",
    "voice": "Synthèse de la voix off...",
    "subtitles": "Transcription des sous-titres...",
    "montage": "Montage final (Ken Burns + encodage)...",
    "done": "Terminé.",
    "error": "Échec.",
}
_STEP_ORDER: list[JobStatus] = ["queued", "script", "images", "voice", "subtitles", "montage", "done"]

# Jobs trop anciens purgés de la mémoire pour ne pas grossir indéfiniment
# sur un process longue durée (voir aussi la limite documentée plus haut).
JOB_TTL_S = 3600


@dataclass
class Job:
    id: str
    prompt: str
    duration: int
    fmt: str
    voice: str
    status: JobStatus = "queued"
    error: str | None = None
    video_path: Path | None = None
    created_at: float = field(default_factory=time.time)


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()


def _purge_old_jobs() -> None:
    cutoff = time.time() - JOB_TTL_S
    with _jobs_lock:
        stale = [jid for jid, j in _jobs.items() if j.created_at < cutoff]
        for jid in stale:
            del _jobs[jid]


def _run_job(job: Job) -> None:
    try:
        job.status = "script"
        story, story_dir = generate_story(job.prompt, duration_s=job.duration)

        job.status = "images"
        generate_images(story, story_dir, fmt=job.fmt)

        job.status = "voice"
        durations = generate_voices(story, story_dir, voice=job.voice)

        job.status = "subtitles"
        srt_path = generate_subtitles(story, story_dir, durations)
        # Libère le modèle whisper avant ffmpeg — sur un hébergement à RAM
        # limitée (ex. Render free, 512 Mo), le garder chargé pendant le
        # montage suffit à déclencher un OOM (constaté en conditions
        # réelles). Voir steps/subtitles.unload_model().
        unload_whisper_model()

        job.status = "montage"
        final_path = generate_montage(story, story_dir, durations, srt_path, fmt=job.fmt)

        job.video_path = final_path
        job.status = "done"
        logger.info("Job %s terminé : %s", job.id, final_path)
    except Exception as e:  # noqa: BLE001 — remonté au client via /status, pas juste loggé
        logger.exception("Job %s a échoué", job.id)
        job.error = str(e)
        job.status = "error"


_INDEX_TEMPLATE = (Path(__file__).parent / "templates" / "index.html").read_text(encoding="utf-8")


def _render_index() -> str:
    options = "".join(
        f'<option value="{fmt}"{" selected" if fmt == config.DEFAULT_FORMAT else ""}>{fmt}</option>'
        for fmt in FORMAT_DIMENSIONS
    )
    return (
        _INDEX_TEMPLATE
        .replace("__FORMAT_OPTIONS__", options)
        .replace("__DEFAULT_VOICE__", config.DEFAULT_VOICE)
    )


app = FastAPI(title="StoryGen")
config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/videos", StaticFiles(directory=str(config.OUTPUT_DIR)), name="videos")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _render_index()


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/generate")
def generate(
    prompt: str = Form(...),
    duration: int = Form(config.DEFAULT_DURATION_S),
    format: str = Form(config.DEFAULT_FORMAT),
    voice: str = Form(config.DEFAULT_VOICE),
) -> JSONResponse:
    _purge_old_jobs()

    prompt = prompt.strip()
    if not prompt:
        return JSONResponse({"error": "Le prompt ne peut pas être vide."}, status_code=400)
    if format not in FORMAT_DIMENSIONS:
        return JSONResponse(
            {"error": f"Format inconnu : {format!r} (attendu : {list(FORMAT_DIMENSIONS)})"}, status_code=400
        )
    if not (5 <= duration <= 600):
        return JSONResponse({"error": "Durée hors bornes raisonnables (5 à 600s)."}, status_code=400)

    job = Job(id=uuid.uuid4().hex[:12], prompt=prompt, duration=duration, fmt=format, voice=voice.strip() or config.DEFAULT_VOICE)
    with _jobs_lock:
        _jobs[job.id] = job
    threading.Thread(target=_run_job, args=(job,), daemon=True, name=f"storygen-job-{job.id}").start()
    return JSONResponse({"job_id": job.id})


@app.get("/status/{job_id}")
def status(job_id: str) -> JSONResponse:
    job = _jobs.get(job_id)
    if job is None:
        return JSONResponse(
            {"status": "error", "error": "Job inconnu (identifiant invalide, ou service redémarré depuis)."},
            status_code=404,
        )
    payload: dict[str, object] = {"status": job.status, "label": STEP_LABELS[job.status]}
    if job.status == "done" and job.video_path is not None:
        payload["video_url"] = f"/videos/{job.video_path.name}"
    if job.status == "error":
        payload["error"] = job.error
    return JSONResponse(payload)
