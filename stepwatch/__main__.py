"""Entrée utilisable via python -m stepwatch et PyInstaller."""

from stepwatch.platform import enable_dpi_awareness


def main():
    enable_dpi_awareness()
    from stepwatch.ui import run_app
    run_app()


if __name__ == "__main__":
    main()
