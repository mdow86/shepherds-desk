# gen_api_call.py
"""
Call Gloo chat completions, validate JSON, write plan.json, print summary.

- Uses paths.OUTPUTS for outputs
- Templates/schemas/inputs resolved via paths.CODE_ROOT
- Plan v1/v2 supported downstream via validators + mappers
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, Any, List

import requests

# Project paths
try:
    import paths
except Exception as e:
    print("Failed to import paths.py — ensure it's on PYTHONPATH:", e, file=sys.stderr)
    sys.exit(2)

# Auth + validation helpers
try:
    from access_token import get_bearer_header, CLIENT_ID, CLIENT_SECRET
except Exception as e:
    print("Missing access_token.py or env; see CLIENT_ID/CLIENT_SECRET:", e, file=sys.stderr)
    sys.exit(2)

try:
    from validators.json_validate import load_schema, parse_and_validate
except Exception as e:
    print("Missing validators.json_validate:", e, file=sys.stderr)
    sys.exit(2)

# Job mapping for summary
try:
    from jobs.mappers import plan_to_image_jobs, plan_to_tts_jobs, summarize_jobs
except Exception as e:
    print("Missing jobs.mappers:", e, file=sys.stderr)
    sys.exit(2)

API_URL = "https://platform.ai.gloo.com/ai/v1/chat/completions"

# Resolve resources relative to the generator code directory
CODE = Path(getattr(paths, "CODE_ROOT", "."))
SCHEMA_PATH = CODE / "schemas" / "plan_schema.json"
PROMPT_TEMPLATE_PATH = CODE / "prompt_templates" / "llm_plan_prompt.txt"
INPUT_PATH = CODE / "inputs" / "user_intent.json"

# Outputs live under packages/generator/outputs
OUTPUTS_DIR = Path(paths.OUTPUTS)
OUTPUT_PLAN_PATH = OUTPUTS_DIR / "plan.json"


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"Missing file: {path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {path}: {e}", file=sys.stderr)
        sys.exit(1)


def load_user_prompt() -> str:
    data = _read_json(INPUT_PATH)
    prompt = (data.get("user_prompt") or "").strip()
    if not prompt:
        print("inputs/user_intent.json missing 'user_prompt'", file=sys.stderr)
        sys.exit(1)
    return prompt


def build_user_message() -> str:
    template = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.replace("{{USER_PROMPT}}", load_user_prompt())


def call_gloo(messages: List[Dict[str, str]]) -> str:
    headers = get_bearer_header(CLIENT_ID, CLIENT_SECRET)
    payload = {
        "model": "meta.llama3-70b-instruct-v1:0",
        "messages": messages,
    }
    r = requests.post(API_URL, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    try:
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        raise RuntimeError(f"Unexpected response: {data}") from e


def main() -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    system_msg = {"role": "system", "content": "You are a human-flourishing assistant."}
    user_msg = {"role": "user", "content": build_user_message()}
    messages = [system_msg, user_msg]

    raw = call_gloo(messages)
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
