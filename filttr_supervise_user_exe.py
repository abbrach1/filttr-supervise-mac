"""Entry point for the user setup GUI executable."""
import sys
from pathlib import Path

# Ensure the package can be imported
sys.path.insert(0, str(Path(__file__).parent))

from filttr_supervise.user_gui import run_user_gui
from filttr_supervise.config import load_config

if __name__ == "__main__":
    run_user_gui(load_config())
