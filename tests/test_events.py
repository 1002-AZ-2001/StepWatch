"""Preview backpressure without losing saved steps or error messages."""

import threading
import unittest
import weakref

from PIL import Image

from stepwatch.events import PreviewEventQueue


class PreviewEventQueueTests(unittest.TestCase):
    def test_paused_consumer_keeps_only_latest_image_and_all_metadata(self):
        events = PreviewEventQueue()
        references = []
        for index in range(50):
            image = Image.new("RGB", (64, 64), (index, 0, 0))
            references.append(weakref.ref(image))
            events.put({"type": "saved", "image": image, "kept": index + 1})
            events.put({"type": "error", "message": f"error {index}"})
        del image
        self.assertEqual(sum(ref() is not None for ref in references), 1)
        items = [events.get_nowait() for _ in range(100)]
        saved = [item for item in items if item["type"] == "saved"]
        self.assertEqual([item["kept"] for item in saved], list(range(1, 51)))
        self.assertEqual(sum("image" in item for item in saved), 1)
        self.assertEqual(saved[-1]["image"].getpixel((0, 0)), (49, 0, 0))
        self.assertEqual([item["message"] for item in items if item["type"] == "error"],
                         [f"error {index}" for index in range(50)])

    def test_producer_does_not_mutate_event_already_given_to_consumer(self):
        events = PreviewEventQueue()
        image = Image.new("RGB", (20, 20), "red")
        original = {"type": "saved", "image": image}
        events.put(original)
        delivered = events.get_nowait()

        def produce():
            for _ in range(50):
                events.put({"type": "saved", "image": Image.new("RGB", (20, 20), "blue")})

        worker = threading.Thread(target=produce)
        worker.start()
        worker.join()
        self.assertIs(delivered["image"], image)
        self.assertIs(original["image"], image)
        self.assertEqual(sum("image" in events.get_nowait() for _ in range(50)), 1)


if __name__ == "__main__":
    unittest.main()
