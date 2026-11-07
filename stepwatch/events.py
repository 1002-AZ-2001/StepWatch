"""Keep event metadata, with at most one pending image for the interface."""

import queue


class PreviewEventQueue(queue.Queue):
    """An unbounded metadata queue with a single most recent queued preview.

    Captures are already on disk; older pending previews can be superseded
    while preserving every step counter, warning and error. Queue's _put and
    _get hooks execute under its mutex, so a dictionary already handed to a
    consumer is never changed by a later producer.
    """

    def __init__(self):
        super().__init__()
        self._pending_preview = None

    def _put(self, item):
        item = dict(item)
        if item.get("type") == "saved" and item.get("image") is not None:
            if self._pending_preview is not None:
                self._pending_preview.pop("image", None)
            self._pending_preview = item
        super()._put(item)

    def _get(self):
        item = super()._get()
        if item is self._pending_preview:
            self._pending_preview = None
        return item
