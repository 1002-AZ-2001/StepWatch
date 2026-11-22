"""Outils communs aux tests d'interface Qt.

Les tests d'interface tournent hors écran (plugin Qt « offscreen ») : ils ne
dépendent ni d'un bureau ni de la souris. Une boîte de dialogue modale non
simulée ferait attendre le test indéfiniment : elle échoue immédiatement.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time  # noqa: E402
from unittest.mock import Mock, patch  # noqa: E402

from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox  # noqa: E402

from stepwatch.models import CaptureTarget, Rect  # noqa: E402
from stepwatch.qtcompat import application, process_events  # noqa: E402

QAPP = application()


def _unexpected_dialog(*_args, **_kwargs):
    raise AssertionError("Boîte de dialogue modale non simulée dans un test.")


for _cls in (QMessageBox, QInputDialog, QDialog):
    _cls.exec = _unexpected_dialog
QFileDialog.getExistingDirectory = staticmethod(_unexpected_dialog)


def default_backend():
    backend = Mock()
    backend.uses_portal = False
    backend.list_windows.return_value = [CaptureTarget("window", "Client", hwnd=1)]
    backend.list_monitors.return_value = [CaptureTarget("monitor", "Écran", rect=Rect(0, 0, 800, 600))]
    backend.resolve.return_value = Rect(0, 0, 800, 600)
    return backend


def make_app(backend=None, *, factory=None, preferences=None, log_path=None, show=True):
    """StepWatchApp avec un moteur de capture simulé (aucune capture réelle)."""
    from stepwatch.ui import StepWatchApp

    if factory is None:
        factory = Mock(return_value=backend if backend is not None else default_backend())
    with patch("stepwatch.ui.capture_backend_factory", return_value=factory):
        app = StepWatchApp(log_path, preferences=preferences)
    if show:
        app.root.show()
    process_events(3)
    return app


def flush_deleted():
    """Détruit les widgets libérés par deleteLater (pas de boucle d'événements dans les tests)."""
    from PySide6.QtCore import QCoreApplication, QEvent

    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def close_app(app):
    try:
        app.shutdown(timeout=5)
    finally:
        app.dispose()
        process_events(3)
        flush_deleted()


def pump(condition=lambda: True, timeout=5, app=None, message="Condition non atteinte"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if app is not None and not app._disposed:
            app._poll()
        process_events()
        if condition():
            return
        time.sleep(.01)
    raise AssertionError(message)


def log_text(app) -> str:
    return app.log.toPlainText()
