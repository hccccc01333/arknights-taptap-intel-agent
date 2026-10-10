"""Run from the repository root; experiments stay outside production runtime."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_v3.benchmark import main

if __name__ == '__main__':
    main()
