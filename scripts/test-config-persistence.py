#!/usr/bin/env python3
"""Run the configuration persistence contract tests with QtTest."""

import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "scripts/config/tests"


def main():
    runner = shutil.which("qmltestrunner6")
    if not runner and Path("/usr/lib/qt6/bin/qmltestrunner").exists():
        runner = "/usr/lib/qt6/bin/qmltestrunner"
    runner = runner or shutil.which("qmltestrunner")
    if not runner:
        print("Qt 6 qmltestrunner is required", file=sys.stderr)
        return 2
    environment = {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",
        "QT_QPA_PLATFORMTHEME": "",
        "QT_QUICK_BACKEND": "software",
    }
    return subprocess.run([runner, "-input", str(TESTS)], env=environment, timeout=30).returncode


if __name__ == "__main__":
    raise SystemExit(main())
