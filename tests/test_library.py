"""Recent guides menu data."""

import os
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

from stepwatch.library import recent_guides
from stepwatch.storage import SessionStore


class RecentGuideTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "guides"

    def guide(self, base, name, age):
        store = SessionStore(base, name)
        store.save(Image.new("RGB", (8, 8)), "initial")
        store.close()
        stamp = time.time() - age
        os.utime(store.directory / "steps.json", (stamp, stamp))
        return store.directory

    def test_guides_folder_and_remembered_folders_newest_first(self):
        old = self.guide(self.output, "Ancien", age=3600)
        new = self.guide(self.output, "Récent", age=10)
        elsewhere = self.guide(self.root / "ailleurs", "Partage réseau", age=600)
        (self.output / "pas-un-guide").mkdir()
        entries = recent_guides(self.output, [str(elsewhere), str(new)])
        self.assertEqual([entry.directory for entry in entries], [new, elsewhere, old])
        self.assertEqual([entry.name for entry in entries], ["Récent", "Partage réseau", "Ancien"])

    def test_missing_folders_links_and_limit(self):
        for index in range(5):
            self.guide(self.output, f"Guide {index}", age=index)
        link = self.root / "lien"
        link.symlink_to(self.guide(self.root / "x", "Cible", age=0), target_is_directory=True)
        entries = recent_guides(self.output, [str(self.root / "disparu"), str(link)], limit=3)
        self.assertEqual(len(entries), 3)
        self.assertNotIn(link, [entry.directory for entry in entries])
        self.assertEqual(recent_guides(self.root / "absent"), [])
        self.assertEqual(recent_guides(None), [])


if __name__ == "__main__":
    unittest.main()
