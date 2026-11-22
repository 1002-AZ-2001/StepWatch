"""Entrée utilisable via python -m stepwatch et PyInstaller."""

import argparse
import logging
import sys

from stepwatch import __version__, diagnostics
from stepwatch.display import display_problem, session_type
from stepwatch.instance import InstanceLock
from stepwatch.platform import enable_dpi_awareness

ALREADY_RUNNING = ("StepWatch est déjà ouvert. Utilisez la fenêtre ou la barre flottante existante : "
                   "deux instances enregistreraient deux fois les mêmes clics.")


def _tell_already_running():
    """Message visible même sans console (exécutable Windows sans fenêtre de commande)."""
    if sys.stderr is not None:
        print(f"StepWatch : {ALREADY_RUNNING}", file=sys.stderr)
    try:
        from stepwatch.qtcompat import application, messagebox
        application()
        messagebox.showinfo("StepWatch", ALREADY_RUNNING)
    except Exception:
        logging.getLogger("stepwatch").exception("Message « déjà ouvert » non affiché")


def _print_latest() -> int:
    """0 : à jour ; 10 : nouvelle version disponible ; 1 : vérification impossible."""
    from stepwatch import updater

    try:
        release = updater.fetch_latest()
    except updater.UpdateError as exc:
        print(f"StepWatch : {exc}", file=sys.stderr or sys.stdout)
        return 1
    installation = updater.detect_installation()
    newer = updater.is_newer(release.version)
    print(f"StepWatch {__version__} ({installation.label}). Dernière version publiée : {release.version}"
          f"{' — mise à jour disponible' if newer else ' — à jour'}. {release.page}")
    return 10 if newer else 0


def _parse(argv):
    parser = argparse.ArgumentParser(prog="stepwatch", description="Documentation locale des interventions.")
    parser.add_argument("--version", action="version", version=f"StepWatch {__version__}")
    parser.add_argument("--self-test", action="store_true",
                        help="vérifie l'installation sur des données synthétiques, puis quitte")
    parser.add_argument("--check-updates", action="store_true",
                        help="affiche la dernière version publiée sur GitHub, puis quitte")
    parser.add_argument("--after-update", action="store_true", help=argparse.SUPPRESS)
    # Unknown arguments (a file manager may pass some) are ignored, as before.
    return parser.parse_known_args(argv)[0]


def main(argv=None):
    arguments = _parse(sys.argv[1:] if argv is None else argv)
    log_path = diagnostics.setup()
    logger = logging.getLogger("stepwatch")
    try:
        dpi_aware = enable_dpi_awareness()
        logger.info("Sensibilité DPI par moniteur : %s.", "oui" if dpi_aware else "non")
        if arguments.self_test:
            from stepwatch import selftest
            return selftest.run()
        if arguments.check_updates:
            return _print_latest()
        if sys.platform.startswith("linux"):
            logger.info("Session graphique : %s.", session_type())
        problem = display_problem()
        if problem:
            logger.error(problem)
            if sys.stderr is not None:
                print(f"StepWatch : {problem}", file=sys.stderr)
            return 2
        lock = InstanceLock()
        # Après une mise à jour, l'ancienne version finit de se fermer.
        if not lock.acquire(wait=60 if arguments.after_update else 0):
            logger.warning("Une autre instance de StepWatch est déjà ouverte.")
            _tell_already_running()
            return 3
        try:
            from stepwatch.updater import cleanup_previous
            if arguments.after_update:
                logger.info("Mise à jour installée : StepWatch %s.", __version__)
            cleanup_previous()  # Ancien exécutable .old d'une mise à jour Windows.
            from stepwatch.ui import run_app
            run_app(log_path)
        finally:
            lock.release()
        return 0
    except Exception:
        logger.exception("Arrêt sur une erreur du thread principal")
        raise
    finally:
        diagnostics.reset()


if __name__ == "__main__":
    raise SystemExit(main())
