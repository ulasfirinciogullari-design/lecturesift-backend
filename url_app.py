"""Render entrypoint for LectureSift.

Loads the stable application and applies the V3.2 low-memory slide engine.
"""
try:
    import app.main as main
except ModuleNotFoundError:
    import main

from lowmem_patch import apply

app = apply(main)
