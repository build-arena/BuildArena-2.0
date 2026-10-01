"""Rebuild Starship Flight 14 from its MCP history and fly the automatic mission; optionally edit cameras first."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from control.run_example import main

if __name__ == "__main__":
    raise SystemExit(main("starship_flight14"))
