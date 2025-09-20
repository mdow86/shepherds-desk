# src/media_generator/helpers/paths.py
from __future__ import annotations

from pathlib import Path
import os

def _find_project_root(start: Path) -> Path:
    """
    Walk upward from `start` to locate the repo root.
    Markers: a .git directory, a tools-local directory, or src/media_generator/.
    """
    for cur in (start,) + tuple(start.parents):
        if (cur / ".git").exists():
            return cur
        if (cur / "tools-local").exists():
            return cur
        if (cur / "src" / "media_generator").exists():
            return cur
    # fallback: two levels up from helpers/
    return start.parent.parent.parent  # helpers -> media_generator -> src -> <root>

# Base directories
_THIS_FILE = Path(__file__).resolve()
_HELPERS_DIR = _THIS_FILE.parent                    # .../src/media_generator/helpers
_CODE_DIR = _HELPERS_DIR.parent                     # .../src/media_generator

_env_base = os.getenv("SDESK_BASE")
if _env_base:
    PROJECT_ROOT = Path(_env_base).resolve()
else:
    PROJECT_ROOT = _find_project_root(_HELPERS_DIR)

# Public constants
CODE_ROOT = _CODE_DIR                               # src/media_generator
HELPERS   = _HELPERS_DIR                            # src/media_generator/helpers

# Data and IO layout (matches your repo)
INPUTS    = HELPERS / "inputs"                      # src/media_generator/helpers/inputs
JOBS      = HELPERS / "jobs"                        # src/media_generator/helpers/jobs
OUTPUTS   = CODE_ROOT / "outputs"                   # src/media_generator/outputs
TEMPLATES = HELPERS / "prompt_templates"            # src/media_generator/helpers/prompt_templates
SCHEMAS   = HELPERS / "schemas"                     # src/media_generator/helpers/schemas
VALIDATORS= HELPERS / "validators"                  # src/media_generator/helpers/validators

# Ensure writable dirs exist
for p in (INPUTS, JOBS, OUTPUTS):
    p.mkdir(parents=True, exist_ok=True)
