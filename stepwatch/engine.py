"""Capture and ordered PNG writing can overlap on separate threads.

Native mouse callbacks hit-test the click with a few stateless Win32 queries,
then only enqueue it. MSS resources are created, used and closed on the capture
thread. Only redacted images reach the writer or the UI. An intervention
allows one submitted write and at most one frame being prepared for it.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import heapq
import logging
import queue
import threading
import time
from typing import Callable

from stepwatch.events import PreviewEventQueue
from stepwatch.imaging import VisualComparator, paint_click, paint_masks, preview_image
from stepwatch.models import CaptureConfig, CaptureTarget
from stepwatch.platform import DEFAULT_DOUBLE_CLICK, CaptureUnavailable, WindowsBackend
from stepwatch.storage import ReportWriteError, SessionStore, StorageError


LOGGER = logging.getLogger(__name__)

#: The interface only shows a preview: send it a bounded copy, resized here on
#: the capture thread, instead of a full-resolution image per saved step.
PREVIEW_MAX_SIZE = (1600, 1000)

#: Queued click whose target-relative position could not be computed by the
#: hook (backend without ``locate_click`` or query failure): the worker
#: re-checks it and reports the error through the usual path.
_UNRESOLVED = object()

#: Windows 7+ silently removes a low-level hook whose callback times out
#: (LowLevelHooksTimeout), and the listener thread stays alive. The watchdog
#: polls the physical button state: a press the hook never reports within
#: this delay means the hook is gone, and it is reinstalled.
HOOK_SILENCE_GRACE = 1.0
#: Back-off ceiling when presses stay unreported after a reinstallation (for
#: instance a higher-integrity window under the cursor, which also filters
#: low-level hooks): never restart the hook in a tight loop.
HOOK_SILENCE_GRACE_MAX = 30.0
#: Hit-tests slower than this in the hook callback are logged: they approach
#: the LowLevelHooksTimeout budget (300 ms by default).
SLOW_HOOK_CALLBACK = 0.1
HOOK_POLL_INTERVAL = 0.05


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
        self._notes = queue.Queue()
        self._thread = None
        self._kept = self._ignored = 0
        self._last_press = None
        self._epoch = 0
        self._locate = None  # Set by the worker before the mouse hook starts.
        self._last_hook_event = float("-inf")  # Monotonic; any press/release.
        self.hook_restarts = 0
        self._slow_hook = 0.0  # Slowest hit-test since last logged, seconds.

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

    def add_note(self, text: str) -> None:
        """Queue a note for the latest step; written by the capture thread.

        Notes are accepted while recording or paused: the store is only ever
        touched by the worker, so a note never races a capture being saved.
        """
        if not str(text).strip():
            raise ValueError("La note est vide.")
        with self._lock:
            if self._state not in ("recording", "paused"):
                raise RuntimeError("Aucun enregistrement en cours.")
            self._notes.put(str(text))
        self._wake.set()

    def _write_notes(self):
        while True:
            try:
                text = self._notes.get_nowait()
            except queue.Empty:
                return
            try:
                step = self.store.add_note(text)
            except ReportWriteError as exc:
                self._emit("error", str(exc), step=exc.step)
            except (StorageError, ValueError) as exc:
                self._emit("error", str(exc))
            else:
                self._emit("note", f"Note ajoutée à l’étape {step['index']}.", step=step)

    def _on_click(self, x, y, button, pressed):
        # A release is not another click. Coalesce duplicate hook notifications,
        # preserving real double-clicks (usually > 30 ms apart).
        now = time.monotonic()
        # Any notification, including releases and paused periods, proves
        # that Windows still calls the hook (see HOOK_SILENCE_GRACE).
        self._last_hook_event = now
        if not pressed:
            return
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
            elapsed = time.monotonic() - now
            if elapsed > SLOW_HOOK_CALLBACK:
                # Logged by the worker: no file I/O inside the native hook.
                self._slow_hook = max(self._slow_hook, elapsed)
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

    def _restart_listener(self, listener):
        """Replace a hook that Windows removed without stopping its thread."""
        try:
            listener.stop()
            listener.join(timeout=2)
        except Exception:
            LOGGER.exception("Arrêt de l'ancien hook souris impossible")
        replacement = self._listener_factory(self._on_click)
        replacement.start()
        self.hook_restarts += 1
        message = ("Le hook souris ne recevait plus les clics (retiré par Windows ?) ; "
                   "il a été réinstallé. Des clics ont pu manquer : vérifiez les dernières étapes.")
        LOGGER.warning("Hook souris réinstallé (%d)", self.hook_restarts)
        self._emit("error" if self.hook_restarts == 1 else "log", message)
        return replacement

    def _ignored_event(self, message):
        self._ignored += 1
        self._emit("ignored", message)

    def _write_capture(self, image, trigger, click, annotation, click_count, timestamp):
        try:
            step = self.store.save(image, trigger, click=click, annotation=annotation,
                                   click_count=click_count, timestamp=timestamp)
        except ReportWriteError as exc:
            step, report_warning = exc.step, str(exc)
        else:
            report_warning = None
        try:
            preview = preview_image(image, PREVIEW_MAX_SIZE)
        except Exception:
            LOGGER.exception("Création de l’aperçu impossible")
            preview = None  # The PNG is already on disk.
        return step, report_warning, preview

    def _run(self):
        backend = listener = None
        pending = []
        last_sequence = None
        comparator = VisualComparator(self.config.conservative) if self.config.mode != "clicks" else None
        writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="StepWatch-écriture")
        writing: Future | None = None
        last_error = None
        report_error = False
        serial = 0
        fatal = False
        visual_due = time.monotonic()
        active_epoch = self._epoch

        def finish_write():
            nonlocal writing, report_error
            if writing is None:
                return
            finished, writing = writing, None
            step, warning, preview = finished.result()
            if warning and not report_error:
                self._emit("error", warning)
            elif not warning and report_error:
                self._emit("log", "Rapport de nouveau à jour.")
            report_error = warning is not None
            self._kept += 1
            self._emit("saved", "Capture enregistrée.", step=step, image=preview)

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
            button_down = getattr(backend, "mouse_button_down", None)
            watch_hook = listener is not None and callable(button_down)
            was_down = False
            previous_poll = time.monotonic()
            next_hook_poll = previous_poll
            unreported_since = unreported_after = None
            grace = HOOK_SILENCE_GRACE
            while not self._stop.is_set():
                self._wake.clear()
                if listener is not None and not listener.is_alive():
                    raise RuntimeError("Le hook souris s’est interrompu ; l’enregistrement est arrêté.")
                if writing is not None and writing.done():
                    finish_write()
                if not self._notes.empty():
                    finish_write()
                    self._write_notes()
                now = time.monotonic()
                if self._slow_hook:
                    slow, self._slow_hook = self._slow_hook, 0.0
                    LOGGER.warning("Localisation du clic lente dans le hook souris : %.0f ms", slow * 1000)
                if watch_hook and now >= next_hook_poll:
                    next_hook_poll = now + HOOK_POLL_INTERVAL
                    try:
                        down = bool(button_down())
                    except Exception:
                        down = False
                    if down and not was_down and unreported_since is None:
                        # The press happened after the previous poll: the
                        # hook must report something from that point on.
                        unreported_since, unreported_after = now, previous_poll
                    was_down, previous_poll = down, now
                    if unreported_since is not None:
                        if self._last_hook_event >= unreported_after - .05:
                            unreported_since = None
                            grace = HOOK_SILENCE_GRACE
                        elif now - unreported_since >= grace:
                            listener = self._restart_listener(listener)
                            unreported_since = None
                            grace = min(grace * 2, HOOK_SILENCE_GRACE_MAX)
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
                click = None
                click_count = 0
                annotation = ""
                trigger = None
                if state == "recording" and pending and pending[0][0] <= now:
                    _, _, pending_click = heapq.heappop(pending)
                    if pending_click is last_sequence:
                        last_sequence = None
                    if pending_click.epoch != epoch:
                        self._ignored_event("Clic en attente annulé par la pause.")
                        continue
                    trigger = "local_click"
                    click, click_count = pending_click.located, pending_click.count
                    annotation = click_annotation(pending_click.button, click_count)
                elif state == "recording" and not pending and comparator is not None and now >= visual_due:
                    trigger = "visual_change"
                if trigger is None:
                    deadlines = []
                    if watch_hook:
                        deadlines.append(next_hook_poll)
                    elif listener is not None:
                        deadlines.append(now + .25)  # Listener liveness without a native probe.
                    if state == "recording":
                        if pending:
                            deadlines.append(pending[0][0])
                        elif comparator is not None:
                            deadlines.append(visual_due)
                    delay = max(0, min(deadlines) - time.monotonic()) if deadlines else None
                    self._wake.wait(delay)
                    continue
                # Pending local clicks defer visual capture, preventing a second
                # visual step for the same delayed local action.
                visual_due = now + self.config.interval
                visual = trigger == "visual_change"
                frame = output = sample = None
                try:
                    rect = backend.resolve(self.target)
                    frame = backend.grab(rect)
                    timestamp = datetime.now(timezone.utc)
                    with self._lock:
                        valid = self._state == "recording" and epoch == self._epoch and not self._stop.is_set()
                        click_arrived = visual and not self._clicks.empty()
                    if not valid:
                        self._ignored_event("Capture annulée par la pause ou l’arrêt.")
                        continue
                    if click_arrived:
                        self._ignored_event("Comparaison différée : un clic local est arrivé pendant la capture.")
                        continue
                    if last_error:
                        self._emit("log", "Cible de nouveau disponible.")
                        last_error = None
                    if frame.mode != "RGB":
                        frame = frame.convert("RGB")
                    paint_masks(frame, self.config.masks)
                    sample = comparator.prepare(frame) if comparator is not None else None
                    if visual and comparator.reference is None:
                        trigger = "initial"
                        annotation = "État initial de la zone sélectionnée."
                    elif visual:
                        percent = comparator.compare(sample)
                        if percent < self.config.sensitivity:
                            self._ignored_event(f"Variation visuelle ignorée ({percent:.2f} %).")
                            continue
                        annotation = f"Changement visuel détecté ({percent:.2f} %) ; origine non déterminée."
                    finish_write()  # Backpressure: never queue a second PNG.
                    output = frame
                    if click is not None:
                        # Only copy when the comparator needs these exact pixels.
                        if sample is not None and sample.pixels is frame:
                            output = frame.copy()
                        paint_click(output, click)
                        paint_masks(output, self.config.masks)
                    # Recheck after analysis and an earlier write. Pause, Stop
                    # and click acceptance are serialized with submission.
                    with self._lock:
                        if self._state != "recording" or epoch != self._epoch or self._stop.is_set():
                            self._ignored_event("Capture annulée par la pause ou l’arrêt.")
                            continue
                        if visual and not self._clicks.empty():
                            self._ignored_event("Comparaison différée : un clic local est arrivé pendant la capture.")
                            continue
                        writing = writer.submit(self._write_capture, output, trigger, click,
                                                annotation, click_count, timestamp)
                    writing.add_done_callback(lambda _result: self._wake.set())
                    if comparator is not None:
                        comparator.reference = sample
                except CaptureUnavailable as exc:
                    if str(exc) != last_error:
                        self._emit("error", str(exc))
                        last_error = str(exc)
                    self._ignored_event("Cible indisponible ; nouvelle tentative automatique.")
                finally:
                    frame = output = sample = None
        except Exception as exc:
            fatal = True
            LOGGER.exception("Enregistrement interrompu")
            self._emit("error", f"Enregistrement interrompu : {exc}")
        finally:
            with self._lock:
                self._locate = None
                self._state = "stopping"  # Prevent new clicks or notes during finalisation.
            if pending or not self._clicks.empty():
                self._emit("log", "Les clics encore en attente ont été annulés à l’arrêt.")
                while not self._clicks.empty():
                    self._clicks.get_nowait()
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
                finish_write()
            except Exception as exc:
                fatal = True
                LOGGER.exception("Écriture de la dernière capture impossible")
                self._emit("error", f"Écriture de la dernière capture impossible : {exc}")
            finally:
                writer.shutdown(wait=True)
            try:
                self._write_notes()  # A note typed just before Stop is kept.
            except Exception as exc:
                fatal = True
                LOGGER.exception("Notes non écrites à l’arrêt")
                self._emit("error", f"Notes non écrites à l’arrêt : {exc}")
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
