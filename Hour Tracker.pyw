"""Double-click to start Hour Tracker in the system tray (no console window)."""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(sys.path[0])

from hourtracker.__main__ import main

main()
