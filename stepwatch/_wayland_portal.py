"""System-Python worker for ScreenCast portals and PipeWire.

This script is run in isolation and does not import the StepWatch package.
The distro owns the GI, GStreamer and PipeWire libraries used here.
"""

import os
import secrets
import socket
import struct
import sys
import time


_BUS_NAME = "org.freedesktop.portal.Desktop"
_OBJECT_PATH = "/org/freedesktop/portal/desktop"
_SCREENCAST = "org.freedesktop.portal.ScreenCast"
_REQUEST = "org.freedesktop.portal.Request"
_SESSION = "org.freedesktop.portal.Session"

# Same private socket protocol as wayland.py.
_HEADER = struct.Struct("!cIIII")
_MAX_FRAME_BYTES = 256 * 1024 * 1024
_MAX_MESSAGE_BYTES = 4096


class _Cancelled(Exception):
    pass


class _PeerClosed(Exception):
    pass


class _Portal:
    def __init__(self, Gio, GLib, context, peer):
        self.Gio, self.GLib, self.context = Gio, GLib, context
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.sender = self.connection.get_unique_name()[1:].replace(".", "_")
        self.session = None
        self.session_subscription = None
        self.closed = False
        self.disconnected = False
        self.loop = None
        self.peer_watch = GLib.unix_fd_source_new(peer.fileno(), GLib.IOCondition.HUP | GLib.IOCondition.ERR)
        self.peer_watch.set_callback(self._on_peer_closed)
        self.peer_watch.attach(context)

    def _on_peer_closed(self, *_args):
        self.disconnected = True
        if self.loop is not None:
            self.loop.quit()
        return False

    def _on_session_closed(self, *_args):
        self.closed = True
        if self.loop is not None:
            self.loop.quit()

    def check(self):
        while self.context.pending():
            self.context.iteration(False)
        if self.disconnected:
            raise _PeerClosed()
        if self.closed:
            raise RuntimeError("Le partage Wayland a été fermé par le bureau.")

    def _call(self, interface, method, arguments, reply_signature):
        return self.connection.call_sync(
            _BUS_NAME, _OBJECT_PATH, interface, method, arguments,
            self.GLib.VariantType.new(reply_signature), self.Gio.DBusCallFlags.NONE, 5000, None,
        )

    def _close(self, path, interface):
        try:
            self.connection.call_sync(_BUS_NAME, path, interface, "Close", None, None,
                                      self.Gio.DBusCallFlags.NONE, 250, None)
        except self.GLib.Error:
            pass  # Already closed or the portal disappeared.

    def _request(self, method, signature, arguments, options):
        self.check()
        token = f"sw{secrets.token_hex(12)}"
        handle = f"{_OBJECT_PATH}/request/{self.sender}/{token}"
        result = None
        loop = self.GLib.MainLoop.new(self.context, False)

        def on_response(_connection, _bus, path, _interface, _signal, parameters, _data):
            nonlocal result
            if path == handle:
                result = parameters.unpack()
                loop.quit()

        def on_timeout(*_args):
            loop.quit()
            return False

        # Subscribe before the method call so a prompt response is not lost.
        # The actual returned handle may differ on a legacy portal.
        subscription = self.connection.signal_subscribe(
            _BUS_NAME, _REQUEST, "Response", None, None, self.Gio.DBusSignalFlags.NONE,
            on_response, None,
        )
        timeout = self.GLib.timeout_source_new_seconds(120)
        timeout.set_callback(on_timeout)
        timeout.attach(self.context)
        self.loop = loop
        try:
            options["handle_token"] = self.GLib.Variant("s", token)
            parameters = self.GLib.Variant(signature, (*arguments, options))
            handle, = self._call(_SCREENCAST, method, parameters, "(o)").unpack()
            loop.run()
            self.check()
        finally:
            self.loop = None
            timeout.destroy()
            self.connection.signal_unsubscribe(subscription)
            if result is None:
                self._close(handle, _REQUEST)

        if result is None:
            raise RuntimeError("Le portail de partage Wayland n’a pas répondu à temps.")
        code, values = result
        if code == 1:
            raise _Cancelled("Partage Wayland annulé dans le portail.")
        if code != 0:
            raise RuntimeError("Le portail a refusé le partage Wayland.")
        return values

    def start(self):
        variant = self.GLib.Variant
        try:
            supported, = self._call(
                "org.freedesktop.DBus.Properties", "Get",
                variant("(ss)", (_SCREENCAST, "AvailableSourceTypes")), "(v)",
            ).unpack()
        except self.GLib.Error as exc:
            raise RuntimeError("Le bureau n’expose pas le portail ScreenCast. Installez ou activez "
                               "le portail de votre bureau avec prise en charge de la capture.") from exc
        types = supported & 3  # MONITOR | WINDOW, only when supported by the desktop.
        if not types:
            raise RuntimeError("Le portail ScreenCast ne propose ni écran ni fenêtre à partager.")

        result = self._request("CreateSession", "(a{sv})", (), {
            "session_handle_token": variant("s", f"sw{secrets.token_hex(12)}"),
        })
        self.session = result["session_handle"]
        self.session_subscription = self.connection.signal_subscribe(
            _BUS_NAME, _SESSION, "Closed", self.session, None, self.Gio.DBusSignalFlags.NONE,
            self._on_session_closed, None,
        )
        self._request("SelectSources", "(oa{sv})", (self.session,), {
            "types": variant("u", types), "multiple": variant("b", False),
        })
        streams = self._request("Start", "(osa{sv})", (self.session, ""), {}).get("streams", ())
        if len(streams) != 1:
            raise RuntimeError("Le portail n’a pas fourni un flux d’écran ou de fenêtre unique.")
        node, _properties = streams[0]
        self.check()
        reply, descriptors = self.connection.call_with_unix_fd_list_sync(
            _BUS_NAME, _OBJECT_PATH, _SCREENCAST, "OpenPipeWireRemote",
            variant("(oa{sv})", (self.session, {})), self.GLib.VariantType.new("(h)"),
            self.Gio.DBusCallFlags.NONE, 5000, None, None,
        )
        index, = reply.unpack()
        return descriptors.get(index), node

    def close(self):
        self.peer_watch.destroy()
        if self.session_subscription is not None:
            self.connection.signal_unsubscribe(self.session_subscription)
        if self.session is not None and not self.closed:
            self._close(self.session, _SESSION)


class _Stream:
    def __init__(self, Gst, GstVideo, descriptor, node):
        self.Gst, self.GstVideo = Gst, GstVideo
        self.pipeline = Gst.Pipeline.new("stepwatch-screencast")
        # StepWatch samples the most recent frame on its own timer. Waiting
        # for a video clock can stall the source when PipeWire timestamps
        # change after negotiation, even with an unsynchronised appsink.
        self.pipeline.use_clock(None)
        source = self._element("pipewiresrc")
        source.set_property("fd", descriptor)
        source.set_property("path", str(node))
        # Decouple the sink's cached sample from the producer's pool. The
        # PipeWire GStreamer plug-in can otherwise stop after exhausting all
        # of the producer's buffers, even when the sink only holds one sample.
        if source.find_property("use-bufferpool") is not None:
            source.set_property("use-bufferpool", False)
        else:
            source.set_property("always-copy", True)
        # Drop empty or invalid buffers before they can displace the latest
        # valid image in the one-slot sink.
        filter_invalid = self._element("identity")
        filter_invalid.set_property("drop-buffer-flags", Gst.BufferFlags.CORRUPTED | Gst.BufferFlags.GAP)
        # Pass through native RGB buffers and convert other source formats
        # such as NV12 or 10-bit video before they reach the app.
        converter = self._element("videoconvert")
        self.sink = self._element("appsink")
        caps = Gst.Caps.from_string("video/x-raw,format=(string){BGRx,RGBx,BGRA,RGBA}")
        self.sink.set_property("caps", caps)
        self.sink.set_property("sync", False)
        # Keep the latest native buffer even if the compositor only submits
        # on damage. Dropping by framerate could lose the final changed frame.
        self.sink.set_property("drop", True)
        self.sink.set_property("max-buffers", 1)
        self.sink.set_property("enable-last-sample", False)
        self.sink.set_property("wait-on-eos", False)
        if not (source.link(filter_invalid) and filter_invalid.link(converter) and converter.link(self.sink)):
            raise RuntimeError("Impossible de relier les composants GStreamer pour la capture Wayland.")
        self.bus = self.pipeline.get_bus()

    def _element(self, name):
        element = self.Gst.ElementFactory.make(name)
        if element is None:
            raise RuntimeError(f"Composant GStreamer « {name} » manquant. Installez le greffon PipeWire "
                               "et les greffons de base de GStreamer.")
        self.pipeline.add(element)
        return element

    def start(self):
        if self.pipeline.set_state(self.Gst.State.PLAYING) == self.Gst.StateChangeReturn.FAILURE:
            self.check()
            raise RuntimeError("Impossible de démarrer le flux de capture PipeWire.")

    def check(self):
        types = self.Gst.MessageType.ERROR | self.Gst.MessageType.EOS
        message = self.bus.pop_filtered(types)
        if message is not None:
            if message.type == self.Gst.MessageType.ERROR:
                error, _detail = message.parse_error()
                raise RuntimeError(f"Flux de capture PipeWire interrompu ({error.message}).")
            raise RuntimeError("Le flux de capture PipeWire a pris fin.")

    def sample(self, wait=0):
        return self.sink.emit("try-pull-sample", wait)

    def dimensions(self, sample):
        caps = sample.get_caps()
        formats = {"BGRx": b"F", "BGRA": b"F", "RGBx": b"X", "RGBA": b"X"}
        kind = formats.get(caps.get_structure(0).get_string("format"))
        info = self.GstVideo.VideoInfo.new_from_caps(caps)
        if kind is None or info is None:
            raise RuntimeError("Format d’image PipeWire non pris en charge.")
        meta = self.GstVideo.buffer_get_video_meta(sample.get_buffer())
        offset, stride = (meta.offset[0], meta.stride[0]) if meta else (info.offset[0], info.stride[0])
        width, height = info.width, info.height
        length = stride * height
        if (not width or not height or stride < width * 4 or length > _MAX_FRAME_BYTES
                or offset + length > sample.get_buffer().get_size()):
            raise RuntimeError("Format d’image PipeWire non pris en charge.")
        return kind, width, height, offset, stride, length

    def send(self, peer, sample):
        kind, width, height, offset, stride, length = self.dimensions(sample)
        buffer = sample.get_buffer()
        ok, mapped = buffer.map(self.Gst.MapFlags.READ)
        if not ok:
            raise RuntimeError("Impossible de lire la mémoire de l’image PipeWire.")
        try:
            peer.sendall(_HEADER.pack(kind, width, height, stride, length))
            with memoryview(mapped.data) as pixels:
                peer.sendall(pixels[offset:offset + length])
        finally:
            buffer.unmap(mapped)

    def close(self):
        self.pipeline.set_state(self.Gst.State.NULL)


def _serve(peer, portal, stream):
    stream.start()
    deadline = time.monotonic() + 10
    first = None
    while first is None:
        portal.check()
        stream.check()
        if time.monotonic() >= deadline:
            raise RuntimeError("Le flux PipeWire ne fournit aucune image. Vérifiez le partage dans votre bureau.")
        first = stream.sample(100_000_000)  # Attend at most 100 ms for a new buffer.
    _, width, height, _, _, _ = stream.dimensions(first)
    peer.sendall(_HEADER.pack(b"R", width, height, 0, 0))

    while True:
        portal.check()
        stream.check()
        peer.settimeout(0.2)
        try:
            command = peer.recv(1)
        except TimeoutError:
            continue
        if not command:
            return
        if command != b"G":
            raise RuntimeError("Commande du flux Wayland inconnue.")
        sample = stream.sample() or first
        first = None
        portal.check()
        peer.settimeout(3)
        if sample is None:
            peer.sendall(_HEADER.pack(b"I", 0, 0, 0, 0))
        else:
            stream.send(peer, sample)
            del sample


def _run(peer):
    try:
        import gi
        gi.require_version("Gst", "1.0")
        gi.require_version("GstVideo", "1.0")
        from gi.repository import Gio, GLib, Gst, GstVideo
    except (ImportError, ValueError) as exc:
        raise RuntimeError("La capture Wayland nécessite PyGObject, GStreamer et les typelibs "
                           "GStreamer du système. Consultez le guide d’installation StepWatch.") from exc

    Gst.init(None)
    context = GLib.MainContext.new()
    context.push_thread_default()
    portal = stream = None
    descriptor = None
    try:
        portal = _Portal(Gio, GLib, context, peer)
        descriptor, node = portal.start()
        stream = _Stream(Gst, GstVideo, descriptor, node)
        _serve(peer, portal, stream)
    finally:
        try:
            if stream is not None:
                stream.close()
            if portal is not None:
                portal.close()
        finally:
            if descriptor is not None:
                os.close(descriptor)
            context.pop_thread_default()


def main():
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM, 0, fileno=int(sys.argv[1])) as peer:
        peer.settimeout(3)
        try:
            _run(peer)
        except (_PeerClosed, BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            kind = b"C" if isinstance(exc, _Cancelled) else b"E"
            reason = str(exc).encode("utf-8", errors="replace")[:_MAX_MESSAGE_BYTES]
            try:
                peer.sendall(_HEADER.pack(kind, 0, 0, 0, len(reason)) + reason)
            except OSError:
                pass  # The main app already stopped or closed the window.


if __name__ == "__main__":
    main()
