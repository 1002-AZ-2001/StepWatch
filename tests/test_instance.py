"""Single StepWatch instance per user session."""

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from stepwatch.instance import InstanceLock, lock_path


@unittest.skipIf(sys.platform == "win32", "flock: Linux et macOS")
class PosixInstanceLockTests(unittest.TestCase):
    def test_second_process_is_refused_until_the_first_exits(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "stepwatch.lock"
            first = InstanceLock(path)
            self.assertTrue(first.acquire())
            script = textwrap.dedent(f"""
                import sys
                from stepwatch.instance import InstanceLock
                sys.exit(0 if InstanceLock({str(path)!r}).acquire() else 3)
            """)
            environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            refused = subprocess.run([sys.executable, "-c", script], env=environment)
            self.assertEqual(refused.returncode, 3)
            first.release()
            accepted = subprocess.run([sys.executable, "-c", script], env=environment)
            self.assertEqual(accepted.returncode, 0)

    def test_lock_location_prefers_the_runtime_directory(self):
        self.assertEqual(lock_path({"XDG_RUNTIME_DIR": "/run/user/1000"}), Path("/run/user/1000/stepwatch.lock"))

    def test_unusable_location_never_blocks_startup(self):
        with tempfile.TemporaryDirectory() as temporary:
            blocker = Path(temporary) / "fichier"
            blocker.write_text("x")
            self.assertTrue(InstanceLock(blocker / "sous" / "stepwatch.lock").acquire())


if __name__ == "__main__":
    unittest.main()
