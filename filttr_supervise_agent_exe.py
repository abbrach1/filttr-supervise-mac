"""Entry point for the Filttr Agent user-side exe.

The user runs this, types the code their admin gives them, and their
phone's USB connection is shared with the admin over the Filttr relay.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from filttr_supervise.agent_gui import run_agent_gui

if __name__ == "__main__":
    run_agent_gui()
