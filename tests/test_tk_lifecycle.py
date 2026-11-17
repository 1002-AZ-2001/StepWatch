"""Destroyed Tcl interpreters must be collected on the GUI thread."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


@unittest.skipUnless(
    os.environ.get("STEPWATCH_GUI_TESTS") == "1"
    and (sys.platform == "win32" or os.environ.get("DISPLAY")),
    "Cycle de vie Tk : activer STEPWATCH_GUI_TESTS=1 avec un bureau Windows ou DISPLAY.",
)
class TkLifecycleTests(unittest.TestCase):
    def test_gui_fixture_releases_tcl_before_a_worker_collects_garbage(self):
        # A native Tcl_AsyncDelete abort cannot be caught by unittest. Isolate
        # this regression and require weakrefs to disappear BEFORE worker GC.
        # The child exercises the actual GUI fixture and StepWatchApp, with
        # only its Windows backend mocked, not a substitute Tk interpreter.
        root = Path(__file__).resolve().parents[1]
        program = textwrap.dedent("""
            import gc
            import sys
            import threading
            import weakref
            from pathlib import Path

            sys.path.insert(0, str(Path.cwd() / 'tests'))
            from test_ui import UITests
            from stepwatch.selftest import check_interface

            gc.disable()
            case = UITests('test_constructor_does_not_record')
            case.setUp()
            root_ref = weakref.ref(case.root)
            app_ref = weakref.ref(case.app)
            case.tearDown()
            if root_ref() is not None or app_ref() is not None:
                raise RuntimeError('GUI cleanup retained the destroyed Tcl interpreter')
            check_interface()
            worker = threading.Thread(target=gc.collect, name='post-GUI-collection')
            worker.start()
            worker.join(timeout=5)
            if worker.is_alive():
                raise RuntimeError('Background garbage collection did not finish')
            print('Tcl interpreter collected on the GUI thread; worker GC completed')
        """)
        result = subprocess.run(
            [sys.executable, "-X", "faulthandler", "-c", program],
            cwd=root, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("worker GC completed", result.stdout)


if __name__ == "__main__":
    unittest.main()
