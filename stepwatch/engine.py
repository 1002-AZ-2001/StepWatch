"""Single capture worker; no keyboard listener and no network access.

Native mouse callbacks only enqueue events. MSS resources are created, used and
closed on the capture thread. Images sent to storage or the UI are redacted.
"""
from __future__ import annotations

import heapq
import queue
import threading
import time
from typing import Callable

from stepwatch.imaging import apply_masks, changed_percent, mark_click
from stepwatch.models import CaptureConfig, CaptureTarget
from stepwatch.platform import CaptureUnavailable, WindowsBackend
from stepwatch.storage import SessionStore


def mouse_listener(callback):
    from pynput import mouse
    return mouse.Listener(on_click=callback)


class Recorder:
    """One intervention. A stopped instance is deliberately not reusable."""

    def __init__(self, target: CaptureTarget, config: CaptureConfig,
                 store: SessionStore, backend_factory: Callable = WindowsBackend,
                 listener_factory: Callable = mouse_listener):
        self.target, self.config, self.store = target, config, store
        self.events = queue.Queue()
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
        with self._lock:
            if self._state != "recording" or self.config.mode == "visual":
                return
            now = time.monotonic()
            key = (int(x), int(y), str(button))
            if self._last_press and key == self._last_press[0] and now - self._last_press[1] < .03:
                return
            self._last_press = (key, now)
            self._clicks.put((now, int(x), int(y), str(button), self._epoch))
        self._wake.set()

    def _ignored_event(self, message):
        self._ignored += 1
        self._emit("ignored", message)

    def _run(self):
        backend = listener = None
        pending = []
        previous = None
        last_error = None
        serial = 0
        fatal = False
        visual_due = time.monotonic()
        active_epoch = self._epoch
        try:
            backend = self._backend_factory()
            # Fail early on unsupported platform/invalid initial selection.
            backend.resolve(self.target)
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
                    visual_due = now
                    active_epoch = epoch
                while True:
                    try:
                        when, x, y, button, click_epoch = self._clicks.get_nowait()
                    except queue.Empty:
                        break
                    with self._lock:
                        click_valid = self._state == "recording" and click_epoch == self._epoch
                    if not click_valid:
                        self._ignored_event("Clic en attente annulé par la pause ou l’arrêt.")
                        continue
                    try:
                        rect = backend.resolve(self.target)
                        if not backend.belongs_to_target(self.target, x, y):
                            continue
                        serial += 1
                        heapq.heappush(pending, (when + self.config.click_delay, serial,
                                                (x - rect.left, y - rect.top), button, click_epoch))
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
                    retained = [item for item in pending if item[4] == epoch]
                    for _ in range(len(pending) - len(retained)):
                        self._ignored_event("Clic en attente annulé par la pause.")
                    pending = retained
                    heapq.heapify(pending)
                    visual_due = now
                    active_epoch = epoch
                if state != "recording":
                    self._wake.wait(.1)
                    continue
                click = None
                annotation = ""
                trigger = None
                if pending and pending[0][0] <= now:
                    _, _, click, button, pending_epoch = heapq.heappop(pending)
                    if pending_epoch != epoch:
                        self._ignored_event("Clic en attente annulé par la pause.")
                        continue
                    trigger = "local_click"
                    annotation = "Clic local (" + button + ")."
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
                    percent = changed_percent(previous, clean, self.config.conservative)
                    if trigger == "visual_change" and previous is not None and percent < self.config.sensitivity:
                        self._ignored_event(f"Variation visuelle ignorée ({percent:.2f} %).")
                        continue
                    if trigger == "visual_change" and previous is None:
                        trigger = "initial"
                        annotation = "État initial de la zone sélectionnée."
                    elif trigger == "visual_change":
                        annotation = f"Changement visuel détecté ({percent:.2f} %) ; origine non déterminée."
                    # Mask again after marker: sensitive rectangles stay entirely
                    # black, including when a local click falls inside one.
                    output = apply_masks(mark_click(clean, click), self.config.masks)
                    step = self.store.save(output, trigger, click=click, annotation=annotation)
                    previous = clean
                    self._kept += 1
                    self._emit("saved", "Capture enregistrée.", step=step, image=output.copy())
                except CaptureUnavailable as exc:
                    if str(exc) != last_error:
                        self._emit("error", str(exc))
                        last_error = str(exc)
                    self._ignored_event("Cible indisponible ; nouvelle tentative automatique.")
        except Exception as exc:
            fatal = True
            self._emit("error", f"Enregistrement interrompu : {exc}")
        finally:
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
                    self._emit("error", f"Erreur de libération des ressources : {exc}")
            try:
                self.store.close()
            except Exception as exc:
                fatal = True
                self._emit("error", f"Erreur d’écriture du rapport : {exc}")
            with self._lock:
                self._state = "error" if fatal else "stopped"
            self._emit("status", "Arrêt avec erreur ; consulter le journal." if fatal else "Enregistrement arrêté ; rapport sauvegardé.")
