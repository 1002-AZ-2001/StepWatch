"""Entrée utilisable via python -m stepwatch et PyInstaller."""

import logging

from stepwatch import diagnostics
from stepwatch.platform import enable_dpi_awareness


def main():
    log_path = diagnostics.setup()
    try:
        dpi_aware = enable_dpi_awareness()
        logging.getLogger("stepwatch").info("Sensibilité DPI par moniteur : %s.", "oui" if dpi_aware else "non")
        from stepwatch.ui import run_app
        run_app(log_path)
    except Exception:
        logging.getLogger("stepwatch").exception("Arrêt sur une erreur du thread principal")
        raise
    finally:
        diagnostics.reset()


if __name__ == "__main__":
    main()
