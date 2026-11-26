#!/usr/bin/env python3
"""One command to run the whole project:

    python run.py

What it does
  1. creates a private virtual environment in .venv/ (first run only)
  2. installs requirements.txt into it (first run, or when requirements change)
  3. runs the data pipeline: checks -> cleaned/combined CSVs -> SQLite -> charts + report
  4. starts the web dashboard at http://127.0.0.1:5000 and opens your browser

Options
  --reset        rebuild the database from the raw files (discards edits made in the app)
  --no-server    run the pipeline only (writes data/processed/ and outputs/), then exit
  --test         run the automated tests, then exit
  --port N       web server port (default 5000)
  --host H       web server host (default 127.0.0.1)
  --no-browser   do not open a browser tab
  --no-venv      use the current Python instead of .venv (you install requirements yourself)
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import threading
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
STAMP = VENV_DIR / ".requirements.sha256"
MIN_PYTHON = (3, 9)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="IT Incident Recurrence & SLA Monitor")
    p.add_argument("--reset", action="store_true", help="rebuild the database from raw data")
    p.add_argument("--no-server", action="store_true", help="run the pipeline only")
    p.add_argument("--test", action="store_true", help="run the test-suite and exit")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5000)))
    p.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    p.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    p.add_argument("--no-venv", action="store_true", help="use the current interpreter")
    return p.parse_args()


# ----------------------------------------------------------------------------- environment
def venv_python() -> Path:
    sub = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    return VENV_DIR / sub


def inside_project_venv() -> bool:
    return Path(sys.prefix).resolve() == VENV_DIR.resolve()


def packages_importable(python: str) -> bool:
    """True if the given interpreter can import every package the app needs."""
    check = "import flask, pandas, matplotlib, pytest"
    return subprocess.call([python, "-c", check], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL) == 0


def ensure_requirements(python: str) -> None:
    """Install requirements.txt into ``python`` when missing or out of date."""
    wanted = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    up_to_date = STAMP.exists() and STAMP.read_text().strip() == wanted
    if up_to_date and packages_importable(python):
        return
    print("» Installing dependencies from requirements.txt (first run only) ...")
    subprocess.check_call([python, "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    subprocess.check_call([python, "-m", "pip", "install", "--quiet", "-r", str(REQUIREMENTS)])
    STAMP.parent.mkdir(parents=True, exist_ok=True)
    STAMP.write_text(wanted)


def ensure_venv() -> None:
    """Create .venv and install requirements if needed, then re-launch inside it."""
    if not venv_python().exists():
        print("» Creating virtual environment in .venv/ (first run only) ...")
        venv.EnvBuilder(with_pip=True, clear=True).create(VENV_DIR)

    ensure_requirements(str(venv_python()))

    # Re-run this same script with the venv's interpreter.
    result = subprocess.call([str(venv_python()), str(Path(__file__).resolve()), *sys.argv[1:]])
    sys.exit(result)


# ----------------------------------------------------------------------------- main
def main() -> None:
    if sys.version_info < MIN_PYTHON:
        sys.exit(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required "
                 f"(you have {sys.version.split()[0]}).")
    args = parse_args()
    if not args.no_venv:
        if inside_project_venv():
            # .venv is already active (e.g. activated by hand) - just make sure
            # the packages are installed in it.
            ensure_requirements(sys.executable)
        else:
            ensure_venv()  # never returns

    sys.path.insert(0, str(ROOT / "src"))
    os.chdir(ROOT)

    if args.test:
        import pytest
        sys.exit(pytest.main(["-q", str(ROOT / "tests")]))

    from incident_monitor import pipeline
    from incident_monitor.app import create_app

    print("\n=== IT Incident Recurrence & SLA Monitor ===")
    pipeline.run(reset_db=args.reset)
    print("\nOutputs:  data/processed/*.csv   outputs/report.md   outputs/charts/*.png")
    if args.no_server:
        return

    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}"
    print(f"\nDashboard running at  {url}   (press Ctrl+C to stop)\n")
    if not args.no_browser and not os.environ.get("CI"):
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    create_app().run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
