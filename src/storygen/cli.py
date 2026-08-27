"""Point d'entrée CLI de StoryGen (``uv run storygen ...``).

Statut actuel — Phase 3 : scénarisation + images + voix + sous-titres +
montage -> premier MP4. ``--dry-run`` est déjà accepté pour figer
l'interface finale, mais ne fait encore rien : il sera branché en Phase 4
sans casser cette commande.
"""

from __future__ import annotations

import argparse
import logging

from storygen import config
from storygen.steps.images import generate_images
from storygen.steps.montage import generate_montage
from storygen.steps.script import generate_story
from storygen.steps.subtitles import generate_subtitles
from storygen.steps.voice import generate_voices

logger = logging.getLogger("storygen.cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="storygen",
        description="Génère une vidéo narrative à partir d'un prompt (pipeline 100% gratuit).",
    )
    parser.add_argument("prompt", help="Sujet de l'histoire, en langage naturel.")
    parser.add_argument(
        "--duration", type=int, default=config.DEFAULT_DURATION_S,
        help=f"Durée totale visée en secondes (défaut : {config.DEFAULT_DURATION_S}).",
    )
    parser.add_argument(
        "--format", choices=["9:16", "16:9"], default=config.DEFAULT_FORMAT,
        help="Format de sortie (résolution des images et de la vidéo finale).",
    )
    parser.add_argument(
        "--voice", default=config.DEFAULT_VOICE,
        help="Voix edge-tts.",
    )
    parser.add_argument("--force", action="store_true", help="Ignore le cache et régénère.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="[Phase 4, pas encore actif] Affiche le plan sans rien générer.",
    )
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args()

    if args.dry_run:
        print("⚠️  --dry-run arrive en Phase 4. Exécution normale de la Phase 1 pour l'instant.\n")

    story, story_dir = generate_story(args.prompt, duration_s=args.duration, force=args.force)
    print(f"\n✅ Phase 1 (scénarisation) : {story_dir / 'story.json'}")
    print(f"   Titre : {story.title}  |  Scènes : {len(story.scenes)}")

    image_paths = generate_images(story, story_dir, fmt=args.format, force=args.force)
    print(f"✅ Phase 2a (images)      : {len(image_paths)} image(s) dans {story_dir / 'assets' / 'img'}")

    durations = generate_voices(story, story_dir, voice=args.voice, force=args.force)
    real_total = sum(durations.values())
    print(f"✅ Phase 2b (voix off)    : {len(durations)} piste(s), durée réelle totale {real_total:.1f}s "
          f"(visée : {args.duration}s)")

    srt_path = generate_subtitles(story, story_dir, durations, force=args.force)
    print(f"✅ Phase 3a (sous-titres) : {srt_path}")

    final_path = generate_montage(story, story_dir, durations, srt_path, fmt=args.format, force=args.force)
    print(f"✅ Phase 3b (montage)     : {final_path}")


if __name__ == "__main__":
    main()
