#!/usr/bin/env python3
"""
Call Gloo chat completions, validate JSON, write plan.json, print summary.

Outputs:
  paths.OUTPUTS / "plan.json"

Resources (resolved via helpers.paths):
  inputs:    paths.INPUTS   (user_intent.json)
  schemas:   paths.SCHEMAS  (plan_schema.json)
  templates: paths.TEMPLATES (llm_plan_prompt.txt)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import requests

# --- project paths (helpers.paths) ---
try:
    # if a shim sits alongside this file
    import paths  # type: ignore
except Exception:
    # canonical location under src/media_generator/helpers
    from helpers import paths  # type: ignore

CODE = Path(paths.CODE_ROOT)            # src/media_generator
OUTPUTS_DIR = Path(paths.OUTPUTS)       # .../packages/generator/outputs
OUTPUT_PLAN_PATH = OUTPUTS_DIR / "plan.json"

SCHEMA_PATH = Path(paths.SCHEMAS) / "plan_schema.json"
PROMPT_TEMPLATE_PATH = Path(paths.TEMPLATES) / "llm_plan_prompt.txt"
INPUT_PATH = Path(paths.INPUTS) / "user_intent.json"

API_URL = "https://platform.ai.gloo.com/ai/v1/chat/completions"

# --- auth + validators + mappers ---
try:
    # canonical location
    from helpers.gloo_access_token import get_bearer_header
except Exception:
    # fallback if this file is run from inside helpers
    from gloo_access_token import get_bearer_header  # type: ignore

try:
    from helpers.validators.json_validate import load_schema, parse_and_validate
except Exception as e:
    print(f"Missing helpers.validators.json_validate: {e}", file=sys.stderr)
    sys.exit(2)

try:
    from helpers.jobs.mappers import plan_to_image_jobs, plan_to_tts_jobs, summarize_jobs
except Exception as e:
    print(f"Missing helpers.jobs.mappers: {e}", file=sys.stderr)
    sys.exit(2)


# ------------------------- small I/O helpers -------------------------

def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"Missing file: {path}", file=sys.stderr); sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {path}: {e}", file=sys.stderr); sys.exit(1)


def load_user_prompt() -> str:
    data = _read_json(INPUT_PATH)
    prompt = (data.get("user_prompt") or "").strip()
    if not prompt:
        print(f"{INPUT_PATH} missing 'user_prompt'", file=sys.stderr); sys.exit(1)
    return prompt


def build_user_message() -> str:
    template = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.replace("{{USER_PROMPT}}", load_user_prompt())


# ------------------------- network call -------------------------

def call_gloo(messages: List[Dict[str, str]], env_file: Path | None) -> str:
    """Obtain Bearer header (reads .env if provided), call chat completions."""
    headers = get_bearer_header(str(env_file) if env_file else None)
    payload = {"model": "meta.llama3-70b-instruct-v1:0", "messages": messages}
    r = requests.post(API_URL, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    try:
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        raise RuntimeError(f"Unexpected response: {data}") from e


# ------------------------- main -------------------------

def main() -> None:
    # Prefer a repo-local .env right under CODE_ROOT (src/media_generator/.env).
    env_file = (CODE.parent.parent) / ".env"
    if not env_file.exists():
        print(f".env not found at {env_file}", file=sys.stderr)
        sys.exit(2)

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    system_msg = {"role": "system", "content": "You are a human-flourishing assistant."}
    user_msg = {"role": "user", "content": build_user_message()}
    messages = [system_msg, user_msg]

    raw = call_gloo(messages, env_file=env_file)
    print("HTTP OK; received model output.")

    schema = load_schema(SCHEMA_PATH)
    plan = parse_and_validate(raw, schema)

    OUTPUT_PLAN_PATH.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(f"Wrote plan → {OUTPUT_PLAN_PATH}")

    image_jobs = plan_to_image_jobs(plan)
    tts_jobs = plan_to_tts_jobs(plan, voice="warm_female")

    title = plan.get("title", "")
    print(f"Title: {title}")
    print(f"Clips: {len(plan.get('clips', []))} | {summarize_jobs(image_jobs, tts_jobs)}")


if __name__ == "__main__":
    main()
