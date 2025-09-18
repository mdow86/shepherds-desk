from pathlib import Path
import os

def _project_root() -> Path:
    env = os.getenv("SDESK_BASE")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[3]  # …/gloo

BASE = _project_root()

CODE_ROOT = Path(__file__).resolve().parent
DATA_ROOT = BASE / "packages" / "generator"      # …/gloo/packages/generator

INPUTS  = DATA_ROOT / "inputs"
JOBS    = DATA_ROOT / "jobs"
OUTPUTS = DATA_ROOT / "outputs"

TEMPLATES = CODE_ROOT / "prompt_templates"
SCHEMAS   = CODE_ROOT / "schemas"

for p in (INPUTS, JOBS, OUTPUTS):
    p.mkdir(parents=True, exist_ok=True)
