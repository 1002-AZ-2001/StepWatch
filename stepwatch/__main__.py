"""Entrée utilisable via python -m stepwatch et PyInstaller."""

import argparse
import logging
import sys

from stepwatch import __version__, diagnostics
from stepwatch.display import display_problem, session_type
from stepwatch.platform import enable_dpi_awareness


def _parse(argv):
    parser = argparse.ArgumentParser(prog="stepwatch", description="Documentation locale des interventions.")
    parser.add_argument("--version", action="version", version=f"StepWatch {__version__}")
    parser.add_argument("--self-test", action="store_true",
                        help="vérifie l'installation sur des données synthétiques, puis quitte")
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
        if sys.platform.startswith("linux"):
            logger.info("Session graphique : %s.", session_type())
        problem = display_problem()
        if problem:
            logger.error(problem)
            if sys.stderr is not None:
                print(f"StepWatch : {problem}", file=sys.stderr)
            return 2
        from stepwatch.ui import run_app
        run_app(log_path)
        return 0
    except Exception:
        logger.exception("Arrêt sur une erreur du thread principal")
        raise
    finally:
        diagnostics.reset()


if __name__ == "__main__":
    raise SystemExit(main())
