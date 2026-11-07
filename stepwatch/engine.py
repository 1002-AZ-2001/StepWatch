"""Single capture worker; no keyboard listener and no network access.

Native mouse callbacks hit-test the click with a few stateless Win32 queries,
then only enqueue it. MSS resources are created, used and closed on the capture
thread. Images sent to storage or the UI are redacted.
"""
from __future__ import annotations

from dataclasses import dataclass
import heapq
import logging
import queue
import threading
import time
from typing import Callable

from stepwatch.events import PreviewEventQueue
from stepwatch.imaging import apply_masks, changed_percent, mark_click
from stepwatch.models import CaptureConfig, CaptureTarget
from stepwatch.platform import DEFAULT_DOUBLE_CLICK, CaptureUnavailable, WindowsBackend
from stepwatch.storage import ReportWriteError, SessionStore


LOGGER = logging.getLogger(__name__)

#: The interface only shows a preview: send it a bounded copy, resized here on
#: the capture thread, instead of a full-resolution image per saved step.
PREVIEW_MAX_SIZE = (1600, 1000)

#: Queued click whose target-relative position could not be computed by the
#: hook (backend without ``locate_click`` or query failure): the worker
#: re-checks it and reports the error through the usual path.
_UNRESOLVED = object()


_BUTTON_NAMES = {
    "Button.left": "gauche", "Button.right": "droit", "Button.middle": "molette",
    "Button.x1": "bouton X1", "Button.x2": "bouton X2",
}


def click_annotation(button: str, count: int = 1) -> str:
    """French step annotation for one or several presses of a mouse button."""
    name = _BUTTON_NAMES.get(button, button)
    if count == 1:
        return f"Clic {name} local."
    if count in (2, 3):
        return f"{'Double' if count == 2 else 'Triple'}-clic {name} local."
    return f"Clic {name} local répété {count} fois."


@dataclass
class _PendingClick:
    """A local click waiting for its delayed capture."""

    when: float  # Monotonic time of the latest press of the sequence.
    x: int  # Desktop position of the first press.
    y: int
    located: tuple[int, int]  # Target-relative position of the first press.
    button: str
    epoch: int
    count: int = 1


def mouse_listener(callback):
    from pynput import mouse
    return mouse.Listener(on_click=callback)


class Recorder:
    """One intervention. A stopped instance is deliberately not reusable."""

    def __init__(self, target: CaptureTarget, config: CaptureConfig,
                 store: SessionStore, backend_factory: Callable = WindowsBackend,
                 listener_factory: Callable = mouse_listener):
        self.target, self.config, self.store = target, config, store
        self.events = PreviewEventQueue()
        self._backend_factory, self._listener_factory = backend_factory, listener_factory
        self._state = "idle"
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._clicks = queue.Queue()
        self._thread = None
        self._kept = self._ignored = 0
        self._last_press = None
        self._epoch = 0
        self._locate = None  # Set by the worker before the mouse hook starts.

    @property
    def state(self):
        with self._lock:
            return self._state

    def _emit(self, kind, message, **extra):
        self.events.put(dict(type=kind, message=message, kept=self._kept,
                             ignored=self._ignored, **extra))

    def start(self):
        with self._lock:
            if self._state != "idle":
                raise RuntimeError("Cet enregistreur a déjà été démarré.")
            self._state = "recording"
            self._thread = threading.Thread(target=self._run, name="StepWatch-capture", daemon=False)
            self._emit("status", "Enregistrement démarré.")
            try:
                self._thread.start()
            except Exception as exc:
                self._thread = None
                self._state = "error"
                self._emit("error", f"Démarrage du thread impossible : {exc}")
                try:
                    self.store.close()
                except Exception as close_error:
                    self._emit("error", f"Erreur d’écriture du rapport : {close_error}")
                raise

    def pause(self):
        with self._lock:
            if self._state != "recording":
                return
            self._state = "paused"
            self._epoch += 1
        self._emit("status", "En pause ; les événements survenus pendant la pause sont ignorés.")
        self._wake.set()

    def resume(self):
        with self._lock:
            if self._state != "paused":
                return
            self._state = "recording"
            self._epoch += 1
        self._emit("status", "Enregistrement repris.")
        self._wake.set()

    def stop(self, timeout=5):
        with self._lock:
            if self._state == "idle":
                self._state = "stopped"
                self.store.close()
                return True
            if self._state not in ("stopped", "error"):
                self._state = "stopping"
            self._stop.set()
            self._wake.set()
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=max(0, timeout))
        return thread is None or not thread.is_alive()

    def _on_click(self, x, y, button, pressed):
        # A release is not another click. Coalesce duplicate hook notifications,
        # preserving real double-clicks (usually > 30 ms apart).
        if not pressed:
            return
        now = time.monotonic()
        x, y = int(x), int(y)
        with self._lock:
            if self._state != "recording" or self.config.mode == "visual":
                return
            locate = self._locate
            epoch = self._epoch
        # Hit-test now: the low-level hook runs before the application reacts
        # (context menu, dialog or dropdown under the cursor, moved window).
        # The worker may be busy for a while and would test too late. Never
        # let an exception escape into the native hook.
        located = _UNRESOLVED
        if locate is not None:
            try:
                located = locate(x, y)
            except Exception:
                located = _UNRESOLVED
            if located is None:
                return  # Outside the target or on another window.
        with self._lock:
            # The native lookup can overlap a complete pause/resume cycle.
            # Keep the epoch from the original press: never revive it as a
            # new action just because recording has since resumed.
            if self._state != "recording" or self._epoch != epoch:
                return
            key = (x, y, str(button), epoch)
            if self._last_press and key == self._last_press[0] and now - self._last_press[1] < .03:
                return
            self._last_press = (key, now)
            self._clicks.put((now, x, y, str(button), epoch, located))
        self._wake.set()

    def _ignored_event(self, message):
        self._ignored += 1
        self._emit("ignored", message)

    def _run(self):
        backend = listener = None
        pending = []
        last_sequence = None
        previous = None
        last_error = None
        report_error = False
        serial = 0
        fatal = False
        visual_due = time.monotonic()
        active_epoch = self._epoch
        try:
            backend = self._backend_factory()
            # Fail early on unsupported platform/invalid initial selection.
            # This also initialises the Win32 adapter before the hook uses it.
            backend.resolve(self.target)
            settings = getattr(backend, "double_click_settings", None)
            double_time, double_dx, double_dy = settings() if callable(settings) else DEFAULT_DOUBLE_CLICK
            locate_click = getattr(backend, "locate_click", None)
            if locate_click is not None:
                target = self.target
                with self._lock:
                    self._locate = lambda x, y: locate_click(target, x, y)
            if self.config.mode != "visual":
                listener = self._listener_factory(self._on_click)
                listener.start()
            while not self._stop.is_set():
                self._wake.clear()
                if listener is not None and not listener.is_alive():
                    raise RuntimeError("Le hook souris s’est interrompu ; l’enregistrement est arrêté.")
                now = time.monotonic()
                with self._lock:
                    state, epoch = self._state, self._epoch
                if epoch != active_epoch:
                    for _ in pending:
                        self._ignored_event("Clic en attente annulé par la pause.")
                    pending.clear()
                    last_sequence = None
                    visual_due = now
                    active_epoch = epoch
                while True:
                    try:
                        when, x, y, button, click_epoch, located = self._clicks.get_nowait()
                    except queue.Empty:
                        break
                    with self._lock:
                        click_valid = self._state == "recording" and click_epoch == self._epoch
                    if not click_valid:
                        self._ignored_event("Clic en attente annulé par la pause ou l’arrêt.")
                        continue
                    try:
                        if located is _UNRESOLVED:
                            rect = backend.resolve(self.target)
                            if not backend.belongs_to_target(self.target, x, y):
                                continue
                            located = (x - rect.left, y - rect.top)
                        # Only the immediately preceding accepted sequence
                        # can grow. Searching older pending sequences turns
                        # A-B-A into a false double-click and reorders steps.
                        sequence = last_sequence
                        if (sequence is not None and sequence.button == button
                                and sequence.epoch == click_epoch
                                and abs(sequence.x - x) <= double_dx
                                and abs(sequence.y - y) <= double_dy
                                and 0 <= when - sequence.when <= double_time):
                            # Next press of a double-click while the first one
                            # is still pending: one step, captured after the
                            # last press so that it shows the result.
                            sequence.count += 1
                            sequence.when = when
                            pending = [(when + self.config.click_delay if item is sequence else due, order, item)
                                       for due, order, item in pending]
                            heapq.heapify(pending)
                            continue
                        serial += 1
                        last_sequence = _PendingClick(when, x, y, located, button, click_epoch)
                        heapq.heappush(pending, (when + self.config.click_delay, serial,
                                                last_sequence))
                    except CaptureUnavailable as exc:
                        self._ignored_event(str(exc))
                        if str(exc) != last_error:
                            self._emit("error", str(exc))
                            last_error = str(exc)
                # Pause/resume can occur while native target resolution runs.
                # Preserve newly queued clicks from the current epoch, only
                # discard pre-pause work rather than relying on an old snapshot.
                with self._lock:
                    state, epoch = self._state, self._epoch
                if epoch != active_epoch:
                    retained = [entry for entry in pending if entry[2].epoch == epoch]
                    for _ in range(len(pending) - len(retained)):
                        self._ignored_event("Clic en attente annulé par la pause.")
                    pending = retained
                    heapq.heapify(pending)
                    if last_sequence is not None and last_sequence.epoch != epoch:
                        last_sequence = None
                    visual_due = now
                    active_epoch = epoch
                if state != "recording":
                    self._wake.wait(.1)
                    continue
                click = None
                click_count = 0
                annotation = ""
                trigger = None
                if pending and pending[0][0] <= now:
                    _, _, pending_click = heapq.heappop(pending)
                    if pending_click is last_sequence:
                        last_sequence = None
                    if pending_click.epoch != epoch:
                        self._ignored_event("Clic en attente annulé par la pause.")
                        continue
                    trigger = "local_click"
                    click, click_count = pending_click.located, pending_click.count
                    annotation = click_annotation(pending_click.button, click_count)
                elif not pending and self.config.mode != "clicks" and now >= visual_due:
                    trigger = "visual_change"
                if trigger is None:
                    self._wake.wait(.05)
                    continue
                # Pending local clicks defer visual capture, preventing a second
                # visual step for the same delayed local action.
                visual_due = now + self.config.interval
                try:
                    rect = backend.resolve(self.target)
                    raw = backend.grab(rect)
                    clean = apply_masks(raw, self.config.masks)
                    with self._lock:
                        valid = self._state == "recording" and epoch == self._epoch and not self._stop.is_set()
                        click_arrived = trigger == "visual_change" and not self._clicks.empty()
                    if not valid:
                        self._ignored_event("Capture annulée par la pause ou l’arrêt.")
                        continue
                    if click_arrived:
                        self._ignored_event("Comparaison différée : un clic local est arrivé pendant la capture.")
                        continue
                    if last_error:
                        self._emit("log", "Cible de nouveau disponible.")
                        last_error = None
                    if trigger == "visual_change" and previous is None:
                        trigger = "initial"
                        annotation = "État initial de la zone sélectionnée."
                    elif trigger == "visual_change":
                        # Only visual samples need the comparison: a local
                        # click is always kept, so skip the costly analysis.
                        percent = changed_percent(previous, clean, self.config.conservative)
                        if percent < self.config.sensitivity:
                            self._ignored_event(f"Variation visuelle ignorée ({percent:.2f} %).")
                            continue
                        annotation = f"Changement visuel détecté ({percent:.2f} %) ; origine non déterminée."
                    # Mask again after marker: sensitive rectangles stay entirely
                    # black, including when a local click falls inside one.
                    output = apply_masks(mark_click(clean, click), self.config.masks)
                    try:
                        step = self.store.save(output, trigger, click=click, annotation=annotation,
                                               click_count=click_count)
                    except ReportWriteError as exc:
                        # The PNG is written and the step kept: the next save
                        # or close() rewrites the report. A file briefly locked
                        # by an antivirus must not end the intervention.
                        step = exc.step
                        if not report_error:
                            self._emit("error", str(exc))
                            report_error = True
                    else:
                        if report_error:
                            self._emit("log", "Rapport de nouveau à jour.")
                            report_error = False
                    previous = clean
                    self._kept += 1
                    preview = output.copy()
                    preview.thumbnail(PREVIEW_MAX_SIZE)
                    self._emit("saved", "Capture enregistrée.", step=step, image=preview)
                except CaptureUnavailable as exc:
                    if str(exc) != last_error:
                        self._emit("error", str(exc))
                        last_error = str(exc)
                    self._ignored_event("Cible indisponible ; nouvelle tentative automatique.")
        except Exception as exc:
            fatal = True
            LOGGER.exception("Enregistrement interrompu")
            self._emit("error", f"Enregistrement interrompu : {exc}")
        finally:
            with self._lock:
                self._locate = None
            if pending or not self._clicks.empty():
                self._emit("log", "Les clics encore en attente ont été annulés à l’arrêt.")
            for resource in (listener, backend):
                if resource is None:
                    continue
                try:
                    if resource is listener:
                        resource.stop()
                        resource.join(timeout=2)
                        if resource.is_alive():
                            raise RuntimeError("Le hook souris ne s’est pas arrêté.")
                    else:
                        resource.close()
                except Exception as exc:
                    fatal = True
                    LOGGER.exception("Libération des ressources impossible")
                    self._emit("error", f"Erreur de libération des ressources : {exc}")
            try:
                self.store.close()
            except Exception as exc:
                fatal = True
                LOGGER.exception("Rapport non écrit à l’arrêt")
                self._emit("error", f"Erreur d’écriture du rapport : {exc}")
            # Queue the last event before publishing the final state: a reader
            # that sees "stopped" or "error" already has every event.
            self._emit("status", "Arrêt avec erreur ; consulter le journal." if fatal else "Enregistrement arrêté ; rapport sauvegardé.")
            with self._lock:
                self._state = "error" if fatal else "stopped"
