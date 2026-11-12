"""Run Notch locally from a source checkout.

    python run.py
    python -m notch

This is a development convenience: it installs the requirements on first use
when PyQt5 or sounddevice is missing, then starts the packaged entry point.
"""

import os
import subprocess
import sys


def main():
    repo_root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(repo_root)
    sys.path.insert(0, repo_root)

    # Ensure dependencies
    try:
        import PyQt5
        import sounddevice
    except ImportError:
        print("Installing dependencies (first run)...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        print("Done. Starting Notch...")

    from notch.app import main as notch_main
    notch_main()


if __name__ == "__main__":
    main()
