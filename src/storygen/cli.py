"""Point d'entrée CLI de StoryGen (``uv run storygen ...``).

Statut actuel — Phase 1 : seule la scénarisation est implémentée.
``--format``, ``--voice`` et ``--dry-run`` sont déjà acceptés pour figer
l'interface finale, mais ne font encore rien : ils seront branchés aux
Phases 2 à 4 sans casser cette commande.
"""

from __future__ import annotations

import argparse
import logging

from storygen import config
from storygen.steps.script import generate_story

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
        help="[Phase 3, pas encore actif] Format de sortie.",
    )
    parser.add_argument(
        "--voice", default=config.DEFAULT_VOICE,
        help="[Phase 2, pas encore actif] Voix edge-tts.",
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

    print(f"\n✅ Phase 1 (scénarisation) terminée : {story_dir / 'story.json'}")
    print(f"   Titre        : {story.title}")
    print(f"   Scènes       : {len(story.scenes)}")
    print(f"   Durée visée  : {args.duration}s  (somme des duration_hint : {story.total_duration_hint:.1f}s)")
    print("\n⚠️  Images, voix, sous-titres et montage arrivent en Phase 2/3 — pas encore exécutés ici.")


if __name__ == "__main__":
    main()
