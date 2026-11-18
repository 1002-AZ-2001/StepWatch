"""Visible desktop capture for native X11 sessions."""

from __future__ import annotations

import logging
import threading

from PIL import Image

from .models import CaptureTarget, Rect
from .platform import CaptureUnavailable


LOGGER = logging.getLogger(__name__)


class _X11Desktop:
    """X11 window queries are independent from the MSS capture connection."""

    def __init__(self, name: str | None) -> None:
        try:
            from Xlib import X, Xatom, display, error
        except ImportError as exc:
            raise CaptureUnavailable("La capture X11 nécessite python-xlib.") from exc

        self.X, self.Xatom, self.error = X, Xatom, error
        self.connection = display.Display(name)
        self.root = self.connection.screen().root

    def _window(self, handle: int):
        return self.connection.create_resource_object("window", handle)

    def windows(self) -> list[tuple[int, str]]:
        def atom(name):
            return self.connection.intern_atom(name, only_if_exists=True)

        clients_atom = atom("_NET_CLIENT_LIST")
        clients = self.root.get_full_property(clients_atom, self.Xatom.WINDOW) if clients_atom else None
        windows = (self.root.query_tree().children if clients is None
                   else [self._window(handle) for handle in clients.value])
        title_atom, utf8_atom = atom("_NET_WM_NAME"), atom("UTF8_STRING")
        titles = []
        for window in windows:
            try:
                attributes = window.get_attributes()
                if attributes.map_state != self.X.IsViewable or attributes.win_class != self.X.InputOutput:
                    continue
                title = window.get_property(title_atom, utf8_atom, 0, 1024) if title_atom and utf8_atom else None
                name = title.value.decode("utf-8", errors="replace") if title else window.get_wm_name()
                if name and name.strip():
                    titles.append((window.id, name.strip()))
            except self.error.XError:
                # A window can disappear between enumeration and title lookup.
                continue
        return titles

    def window_rect(self, handle: int) -> Rect:
        window = self._window(handle)
        try:
            if window.get_attributes().map_state != self.X.IsViewable:
                raise CaptureUnavailable("La fenêtre X11 est masquée ou réduite. Restaurez-la.")
            geometry = window.get_geometry()
            position = self.root.translate_coords(window, 0, 0)
            if not position.same_screen:
                raise CaptureUnavailable("La fenêtre appartient à un autre écran X11.")
            return Rect(position.x, position.y, geometry.width, geometry.height)
        except (self.error.BadWindow, self.error.BadDrawable) as exc:
            raise CaptureUnavailable("La fenêtre X11 sélectionnée a été fermée.") from exc

    def desktop_rect(self) -> Rect:
        geometry = self.root.get_geometry()
        return Rect(0, 0, geometry.width, geometry.height)

    def window_at(self, handle: int, x: int, y: int) -> bool:
        branch = []
        parent = self.root
        try:
            # TranslateCoords returns the topmost mapped child at the point.
            # Descend through window manager frames and nested widgets.
            for _ in range(32):
                child = parent.translate_coords(self.root, x, y).child
                if not child:
                    break
                if child.id == handle:
                    return True
                branch.append(child)
                parent = child
            for window in reversed(branch):
                for _ in range(16):
                    owner = window.get_wm_transient_for()
                    if owner is None:
                        break
                    if owner.id == handle:
                        return True
                    window = owner
        except self.error.XError:
            return False
        return False

    def close(self) -> None:
        self.connection.close()


class X11Backend:
    """An MSS instance belongs to the thread that captures from it.

    Python Xlib serializes its connection requests. The mouse hook can query the
    selected window while the capture worker gathers pixels through MSS.
    """

    def __init__(self, *, display: str | None = None) -> None:
        self._display = display
        self._desktop: _X11Desktop | None = None
        self._capture = None
        self._owner: int | None = None

    def _x11(self) -> _X11Desktop:
        if self._desktop is None:
            try:
                self._desktop = _X11Desktop(self._display)
            except CaptureUnavailable:
                raise
            except Exception as exc:
                raise CaptureUnavailable(f"Connexion à l’affichage X11 impossible : {exc}") from exc
        return self._desktop

    def _open_capture(self):
        from mss import MSS

        return MSS(display=self._display)

    def _screen(self):
        if self._owner is not None and self._owner != threading.get_ident():
            raise CaptureUnavailable("La capture X11 appartient à un autre thread.")
        if self._capture is None:
            self._capture = self._open_capture()
            self._owner = threading.get_ident()
        return self._capture

    def list_windows(self) -> list[CaptureTarget]:
        try:
            windows = self._x11().windows()
        except CaptureUnavailable:
            raise
        except Exception as exc:
            raise CaptureUnavailable(f"Énumération des fenêtres X11 impossible : {exc}") from exc
        return sorted((CaptureTarget("window", title, hwnd=handle) for handle, title in windows),
                      key=lambda target: target.label.casefold())

    def list_monitors(self) -> list[CaptureTarget]:
        try:
            # MSS caches the monitor layout per instance. Refresh should use a
            # fresh connection so hot-plugged displays can be selected.
            with self._open_capture() as screen:
                return [CaptureTarget("monitor", f"Moniteur {index} ({rect.width} × {rect.height})", rect=rect)
                        for index, monitor in enumerate(screen.monitors[1:], start=1)
                        for rect in (Rect.from_any(monitor),)]
        except Exception as exc:
            raise CaptureUnavailable(f"Énumération des moniteurs X11 impossible : {exc}") from exc

    def resolve(self, target: CaptureTarget) -> Rect:
        if target.kind == "window":
            try:
                return self._x11().window_rect(target.hwnd)
            except CaptureUnavailable:
                raise
            except Exception as exc:
                raise CaptureUnavailable(f"Dimensions de la fenêtre X11 indisponibles : {exc}") from exc
        if target.rect is None:
            raise CaptureUnavailable("La zone de capture X11 n’est pas définie.")
        return target.rect

    def locate_click(self, target: CaptureTarget, x: int, y: int) -> tuple[int, int] | None:
        rectangle = self.resolve(target)
        if not rectangle.contains(x, y):
            return None
        if target.kind == "window" and not self._x11().window_at(target.hwnd, x, y):
            return None
        return x - rectangle.left, y - rectangle.top

    def grab(self, rectangle: Rect) -> Image.Image:
        try:
            desktop = self._x11().desktop_rect()
            left = max(rectangle.left, desktop.left)
            top = max(rectangle.top, desktop.top)
            right = min(rectangle.right, desktop.right)
            bottom = min(rectangle.bottom, desktop.bottom)
            if right <= left or bottom <= top:
                raise CaptureUnavailable("La zone sélectionnée est entièrement hors du bureau X11.")
            if (Image.MAX_IMAGE_PIXELS is not None
                    and rectangle.width * rectangle.height > Image.MAX_IMAGE_PIXELS):
                raise CaptureUnavailable("La zone de capture X11 est trop grande.")
            visible = Rect(left, top, right - left, bottom - top)
            shot = self._screen().grab(visible.as_dict())
            image = Image.frombytes("RGB", shot.size, shot.raw, "raw", "BGRX")
        except CaptureUnavailable:
            raise
        except Exception as exc:
            raise CaptureUnavailable(f"Capture de l’image X11 impossible : {exc}") from exc
        if visible == rectangle:
            return image
        # X11 rejects reads extending outside the root. Preserve the requested
        # dimensions so mask and click coordinates keep their original origin.
        output = Image.new("RGB", (rectangle.width, rectangle.height))
        output.paste(image, (left - rectangle.left, top - rectangle.top))
        return output

    def close(self) -> None:
        if self._owner is not None and self._owner != threading.get_ident():
            raise CaptureUnavailable("La capture X11 doit être fermée par son thread propriétaire.")
        try:
            if self._capture is not None:
                self._capture.close()
        finally:
            self._capture = self._owner = None
            if self._desktop is not None:
                desktop, self._desktop = self._desktop, None
                desktop.close()


class X11ClickListener(threading.Thread):
    """Read only button presses, with a separate connection for stopping XRecord."""

    _BUTTONS = {
        1: "Button.left", 2: "Button.middle", 3: "Button.right", 8: "Button.x1", 9: "Button.x2",
    }

    def __init__(self, callback) -> None:
        super().__init__(name="StepWatch-clics-X11", daemon=True)
        self._callback = callback
        self._control = None
        self._context = None
        self._guard = threading.Lock()
        self._stopping = threading.Event()
        self._recording = False

    def start(self) -> None:
        from Xlib import X, display
        from Xlib.ext import record

        if self.ident is not None or self._control is not None:
            raise RuntimeError("L’écoute des clics X11 a déjà été démarrée.")
        self._control = display.Display()
        try:
            if not self._control.has_extension("RECORD"):
                raise CaptureUnavailable("Ce serveur X11 ne propose pas RECORD. Utilisez le mode visuel.")
            self._context = self._control.record_create_context(0, [record.AllClients], [{
                "core_requests": (0, 0),
                "core_replies": (0, 0),
                "ext_requests": (0, 0, 0, 0),
                "ext_replies": (0, 0, 0, 0),
                "delivered_events": (0, 0),
                "device_events": (X.ButtonPress, X.ButtonPress),
                "errors": (0, 0),
                "client_started": False,
                "client_died": False,
            }])
            # Ensure the context exists on the server before another client
            # starts receiving from it.
            self._control.sync()
            super().start()
        except BaseException:
            self._control.close()
            self._control = None
            raise

    def _disable(self) -> None:
        # Caller holds _guard. The connection receiving the stream cannot
        # process requests until EnableContext receives EndOfData.
        if self._recording:
            self._recording = False
            self._control.record_disable_context(self._context)
            self._control.flush()

    def stop(self) -> None:
        self._stopping.set()
        with self._guard:
            self._disable()

    def run(self) -> None:
        from Xlib import X, display
        from Xlib.ext import record
        from Xlib.protocol import rq

        stream = None
        try:
            if self._stopping.is_set():
                return
            stream = display.Display()
            single_screen = stream.screen_count() == 1
            root = self._control.screen().root
            parser = rq.EventField(None)

            def receive(reply):
                if reply.category == record.StartOfData:
                    with self._guard:
                        self._recording = True
                        if self._stopping.is_set():
                            self._disable()
                    return
                if reply.category != record.FromServer or reply.client_swapped:
                    return
                data = reply.data
                while data and not self._stopping.is_set():
                    event, data = parser.parse_binary_value(data, stream.display, None, None)
                    if event.type == X.ButtonPress:
                        button = self._BUTTONS.get(event.detail)
                        # Raw XRecord device events have no root window ID.
                        # Query the pointer only on servers with multiple X
                        # screens, which differ from RandR monitor layouts.
                        if button is not None and (single_screen or root.query_pointer().same_screen):
                            self._callback(event.root_x, event.root_y, button, True)

            stream.record_enable_context(self._context, receive)
        except Exception:
            LOGGER.exception("Interruption de l’écoute des clics X11")
        finally:
            try:
                if stream is not None:
                    stream.close()
            finally:
                with self._guard:
                    self._recording = False
                    self._control.close()  # Releases its server-side context.
                    self._control = None
