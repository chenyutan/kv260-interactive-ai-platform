"""One reversible platformer entry choice, without loading GTK or game data."""
import os
from pathlib import Path


def selected(root=None, environ=None):
    environ = os.environ if environ is None else environ
    if environ.get('INK_PLATFORMER_ENGINE') == 'legacy': return False
    root = Path(root or Path(__file__).resolve().parent.parent)
    return (root / 'thextech/runtime.json').is_file()


def entry(root, environ=None):
    root = Path(root)
    return root / ('thextech' if selected(root, environ) else 'platformer') / 'main.py'
