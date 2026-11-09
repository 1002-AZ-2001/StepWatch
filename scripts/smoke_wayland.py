#!/usr/bin/env python3
"""Check StepWatch in a real Wayland session (headless weston + XWayland).

1. With XWayland: ``EXECUTABLE --self-test`` must open and close the real
   interface and report a Wayland session.
2. Without XWayland (no DISPLAY): StepWatch must exit with code 2 and an
   explanation naming XWayland, instead of a Tk traceback.

Requires weston (9 or later) and XWayland. Used by the CI on Arch Linux.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def xwayland_arguments() -> list[str]:
    """weston >= 10 takes --xwayland and refuses the old module form."""
    help_text = subprocess.run(["weston", "--help"], capture_output=True, text=True).stdout
    return ["--xwayland"] if "--xwayland" in help_text else ["--modules=xwayland.so"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("executable", help="commande StepWatch, par exemple /usr/bin/stepwatch")
    arguments = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="stepwatch-wayland-") as runtime:
        os.chmod(runtime, 0o700)
        environment = {key: value for key, value in os.environ.items()
                       if key not in {"DISPLAY", "WAYLAND_DISPLAY"}}
        environment.update(XDG_RUNTIME_DIR=runtime, XDG_SESSION_TYPE="wayland",
                           XDG_STATE_HOME=str(Path(runtime) / "state"))
        log = Path(runtime) / "weston.log"
        with log.open("wb") as handle:
            weston = subprocess.Popen(
                ["weston", "--backend=headless-backend.so", "--socket=wayland-stepwatch",
                 "--idle-time=0", *xwayland_arguments()],
                env=environment, stdout=handle, stderr=subprocess.STDOUT)
        try:
            display = None
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and display is None:
                if weston.poll() is not None:
                    raise RuntimeError(f"weston s'est arrêté :\n{log.read_text(errors='replace')}")
                match = re.search(r"xserver listening on display (:\d+)", log.read_text(errors="replace"))
                display = match.group(1) if match else None
                time.sleep(.1)
            if display is None:
                raise RuntimeError(f"XWayland n'a pas démarré :\n{log.read_text(errors='replace')}")
            environment["WAYLAND_DISPLAY"] = "wayland-stepwatch"

            with_xwayland = dict(environment, DISPLAY=display)
            result = subprocess.run([arguments.executable, "--self-test"], env=with_xwayland,
                                    capture_output=True, text=True, timeout=120)
            print(result.stdout, end="")
            if (result.returncode != 0 or "Interface Tk : OK" not in result.stdout
                    or "session wayland" not in result.stdout):
                raise RuntimeError(f"Auto-test sous Wayland en échec ({result.returncode}) :\n"
                                   f"{result.stdout}{result.stderr}")

            result = subprocess.run([arguments.executable], env=environment,
                                    capture_output=True, text=True, timeout=60)
            if result.returncode != 2 or "XWayland" not in result.stderr:
                raise RuntimeError(f"Sans XWayland, code {result.returncode} inattendu :\n"
                                   f"{result.stdout}{result.stderr}")
            print("Sans XWayland : message explicite, code 2.")
        finally:
            weston.terminate()
            try:
                weston.wait(timeout=10)
            except subprocess.TimeoutExpired:
                weston.kill()
    print("Wayland (weston + XWayland) : OK.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"Échec : {exc}", file=sys.stderr)
        raise SystemExit(1)
