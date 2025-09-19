# paths.py
from pathlib import Path
import os

def _find_project_root(start: Path) -> Path:
    """
    Walk upward from `start` to locate the repo root.
    Heuristics: a folder containing orchestrate.py or a .git directory.
    Fallback: three parents up (old behavior), but only if found.
    """
    for cur in (start,) + tuple(start.parents):
        if (cur / "orchestrate.py").exists() or (cur / ".git").exists():
            return cur
        # if your repo has a 'packages/generator' folder, use that marker too:
        if (cur / "packages" / "generator").exists():
            return cur
    # last resort: do NOT return drive root; return start's parent sensibly
    return start.parent

# If SDESK_BASE is set, use it. Otherwise, autodetect from this file's location.
_env = os.getenv("SDESK_BASE")
if _env:
    BASE = Path(_env).resolve()
else:
    CODE_ROOT = Path(__file__).resolve().parent
    BASE = _find_project_root(CODE_ROOT)

# Code lives next to this file
CODE_ROOT = Path(__file__).resolve().parent

# Data tree for the generator package
DATA_ROOT = BASE / "packages" / "generator"

INPUTS   = DATA_ROOT / "inputs"
JOBS     = DATA_ROOT / "jobs"
OUTPUTS  = DATA_ROOT / "outputs"          # <— single, canonical outputs dir

TEMPLATES = CODE_ROOT / "prompt_templates"
SCHEMAS   = CODE_ROOT / "schemas"

for p in (INPUTS, JOBS, OUTPUTS):
    p.mkdir(parents=True, exist_ok=True)
