"""Étape 3a — Sous-titres : assets/audio/scene_XX.mp3 -> assets/subtitles.srt
(faster-whisper, en local, sans clé et gratuit).

Constat réel : le débit et les pauses réels d'edge-tts ne correspondent
jamais exactement à ``duration_hint`` (déjà mesuré en Phase 2b via
``probe_duration``). Plutôt que de dériver des timestamps approximatifs à
partir du texte de narration, on transcrit chaque piste audio réelle avec
faster-whisper pour obtenir un chronométrage fiable, puis on décale les
timestamps de chaque scène par son offset cumulé dans la timeline finale —
les scènes étant concaténées bout à bout au montage (Phase 3b) — pour
produire un seul fichier SRT chronométré sur toute la vidéo.

Chemin rapide (par défaut) : steps/voice.py enregistre le minutage mot à mot
renvoyé par edge-tts (``scene_XX.words.json``). Les sous-titres en sont
construits directement — texte exact du scénario, ponctuation comprise, par
courts groupes de mots — sans charger ni exécuter faster-whisper. Constat
mesuré : whisper "tiny" met ~30 s à se charger et ~0,2 s de CPU par seconde
d'audio, soit plusieurs minutes sur le 0,1 CPU de Render free. Whisper ne
sert plus que de repli pour un MP3 sans minutage.

Appelable seul :
    uv run python -m storygen.steps.subtitles work/{slug}/story.json
"""

from __future__ import annotations

import argparse
import gc
import json
import logging
from pathlib import Path

from faster_whisper import WhisperModel

from storygen import config
from storygen.models import Story
from storygen.utils.cache import is_fresh
from storygen.steps.voice import words_path_for
from storygen.utils.ffmpeg import probe_duration

logger = logging.getLogger("storygen.subtitles")

MAX_LINE_CHARS = 56  # limite le nombre de lignes affichées simultanément (voir constat "limite connue" au README)
MIN_SRT_SIZE = 10

# Groupement des mots en sous-titres (chemin edge-tts) : courts groupes sur une
# ligne, coupés en fin de phrase ou sur une pause, plutôt que de longs segments
# whisper affichés sur 3-4 lignes (voir "limite connue" au README).
MAX_CUE_CHARS = 42
CUE_BREAK_PAUSE_S = 0.6
_CUE_END_PUNCTUATION = (".", "!", "?", "…", ":", ";")

# Chargé une seule fois par process : le modèle "small" (~500 Mo, téléchargé
# depuis Hugging Face au premier lancement) prend plusieurs secondes à
# initialiser, ce serait gâché si on le rechargeait par scène.
_model: WhisperModel | None = None


def unload_model() -> None:
    """Libère le modèle chargé en mémoire. Sans effet s'il n'a jamais été
    chargé. Constat réel (hébergement à 512 Mo, ex. Render free) : le
    modèle whisper reste sinon en mémoire pendant que ffmpeg démarre pour
    le montage juste après — assez pour déclencher un OOM. webapp.py
    appelle cette fonction entre les deux étapes ; la CLI locale (RAM non
    contrainte) n'en a pas besoin et ne l'appelle pas."""
    global _model
    if _model is not None:
        del _model
        _model = None
        gc.collect()


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        logger.info("Chargement du modèle faster-whisper (%s, CPU)...", config.WHISPER_MODEL_SIZE)
        _model = WhisperModel(config.WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
    return _model


def _format_timestamp(seconds: float) -> str:
    """Formate en ``HH:MM:SS,mmm`` (format SRT)."""
    total_ms = round(max(seconds, 0.0) * 1000)
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    secs, ms = divmod(rem_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def wrap_subtitle_text(text: str, max_chars: int = MAX_LINE_CHARS) -> str:
    """Coupe un segment sur des espaces pour rester lisible à l'écran, sans
    jamais couper un mot. Ne force pas à exactement 2 lignes : un segment
    court reste sur une seule ligne."""
    words = text.split()
    if not words:
        return text
    lines: list[str] = [words[0]]
    for word in words[1:]:
        candidate = f"{lines[-1]} {word}"
        if len(candidate) <= max_chars:
            lines[-1] = candidate
        else:
            lines.append(word)
    return "\n".join(lines)


def _transcribe_scene(audio_path: Path) -> list[tuple[float, float, str]]:
    """Retourne ``[(start_s, end_s, texte)]`` relatifs au début du fichier."""
    model = _get_model()
    segments, _info = model.transcribe(str(audio_path), language="fr", vad_filter=True)
    return [(seg.start, seg.end, seg.text.strip()) for seg in segments if seg.text.strip()]


def _words_with_punctuation(
    narration: str, words: list[tuple[float, float, str]]
) -> list[tuple[float, float, str]]:
    """edge-tts renvoie les mots sans ponctuation : on les retrouve dans la
    narration pour récupérer le texte exact jusqu'au mot suivant (virgules,
    points, guillemets...). Un mot introuvable garde son texte brut."""
    positions: list[int | None] = []
    cursor = 0
    for _start, _end, word in words:
        found = narration.find(word, cursor)
        positions.append(found if found >= 0 else None)
        if found >= 0:
            cursor = found + len(word)

    result: list[tuple[float, float, str]] = []
    for k, (start, end, word) in enumerate(words):
        pos = positions[k]
        text = word
        if pos is not None:
            following = [p for p in positions[k + 1:] if p is not None]
            text = narration[pos : following[0] if following else len(narration)].strip() or word
        result.append((start, end, text))
    return result


def cues_from_words(
    narration: str, words: list[tuple[float, float, str]]
) -> list[tuple[float, float, str]]:
    """Regroupe le minutage mot à mot en sous-titres ``[(début, fin, texte)]``
    relatifs au début de la scène."""
    cues: list[tuple[float, float, str]] = []
    current: list[tuple[float, float, str]] = []

    def flush() -> None:
        if current:
            cues.append((current[0][0], current[-1][1], " ".join(w for _s, _e, w in current)))
            current.clear()

    for start, end, text in _words_with_punctuation(narration, words):
        if current:
            joined = " ".join(w for _s, _e, w in current) + " " + text
            if len(joined) > MAX_CUE_CHARS or start - current[-1][1] > CUE_BREAK_PAUSE_S:
                flush()
        current.append((start, end, text))
        if text.rstrip("»\"' ").endswith(_CUE_END_PUNCTUATION):
            flush()
    flush()

    # Prolonge chaque sous-titre jusqu'au suivant quand l'écart est court :
    # évite un clignotement entre deux groupes de mots enchaînés.
    for i in range(len(cues) - 1):
        start, end, text = cues[i]
        next_start = cues[i + 1][0]
        if 0 < next_start - end <= CUE_BREAK_PAUSE_S:
            cues[i] = (start, next_start, text)
    return cues


def _scene_cues(narration: str, audio_path: Path) -> list[tuple[float, float, str]]:
    """Sous-titres d'une scène : minutage edge-tts s'il existe, sinon
    transcription faster-whisper (repli)."""
    words_path = words_path_for(audio_path)
    if words_path.is_file():
        words = [tuple(w) for w in json.loads(words_path.read_text(encoding="utf-8"))]
        if words:
            return cues_from_words(narration, words)  # type: ignore[arg-type]
    logger.info("%s : pas de minutage edge-tts, transcription faster-whisper...", audio_path.name)
    return _transcribe_scene(audio_path)


def build_srt(entries: list[tuple[float, float, str]]) -> str:
    """Construit le contenu d'un fichier SRT à partir d'entrées déjà
    décalées sur la timeline finale."""
    blocks = []
    for i, (start, end, text) in enumerate(entries, start=1):
        blocks.append(
            f"{i}\n{_format_timestamp(start)} --> {_format_timestamp(end)}\n{wrap_subtitle_text(text)}\n"
        )
    return "\n".join(blocks)


def generate_subtitles(
    story: Story,
    story_dir: Path,
    durations: dict[int, float],
    force: bool = False,
) -> Path:
    """Transcrit chaque scène et écrit un SRT unique chronométré sur la
    timeline finale du montage (scènes concaténées bout à bout, sans
    silence entre elles — voir steps/montage.py)."""
    audio_dir = story_dir / "assets" / "audio"
    srt_path = story_dir / "assets" / "subtitles.srt"

    if not force and is_fresh(srt_path, min_size=MIN_SRT_SIZE):
        logger.info("Cache hit : %s", srt_path)
        return srt_path

    entries: list[tuple[float, float, str]] = []
    offset = 0.0
    total = len(story.scenes)
    for scene in story.scenes:
        audio_path = audio_dir / f"scene_{scene.index:02d}.mp3"
        if not audio_path.is_file():
            raise FileNotFoundError(
                f"Audio manquant pour la scène {scene.index} : {audio_path} "
                "(lance la Phase 2b avant la Phase 3a)."
            )
        logger.info("Scène %d/%d : sous-titres...", scene.index, total)
        for start, end, text in _scene_cues(scene.narration, audio_path):
            entries.append((offset + start, offset + end, text))
        offset += durations.get(scene.index) or probe_duration(audio_path)

    if not entries:
        raise RuntimeError("Aucun sous-titre transcrit — vérifie les fichiers audio des scènes.")

    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text(build_srt(entries), encoding="utf-8")
    logger.info("-> %s (%d sous-titre(s))", srt_path, len(entries))
    return srt_path


def _cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="StoryGen — Phase 3a : sous-titres seuls")
    parser.add_argument("story_path", type=Path, help="Chemin vers story.json")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    story = Story.model_validate_json(args.story_path.read_text(encoding="utf-8"))
    story_dir = args.story_path.parent
    audio_dir = story_dir / "assets" / "audio"
    durations = {
        scene.index: probe_duration(audio_dir / f"scene_{scene.index:02d}.mp3")
        for scene in story.scenes
    }
    srt_path = generate_subtitles(story, story_dir, durations, force=args.force)
    print(f"\n✅ Sous-titres générés : {srt_path}")


if __name__ == "__main__":
    _cli()
