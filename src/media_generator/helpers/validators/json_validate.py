from __future__ import annotations
import json
import sys
from pathlib import Path
from jsonschema import Draft7Validator

def load_schema(schema_path: Path) -> dict:
    with schema_path.open("r", encoding="utf-8") as f:
        return json.load(f)

def parse_and_validate(raw_text: str, schema: dict) -> dict:
    s = raw_text.strip()
    try:
        data = json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e.msg} at pos {e.pos}") from e

    # Schema validation
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: e.path)
    if errors:
        msgs = []
        for e in errors:
            path = "/".join(map(str, e.path)) or "(root)"
            msgs.append(f"{path}: {e.message}")
        raise ValueError("Schema validation failed: " + " | ".join(msgs))

    clips = data["clips"]

    # Monotonic time and duration checks
    prev_end = -1.0
    for i, c in enumerate(clips, start=1):
        if c["index"] != i:
            raise ValueError(f"clips[{i-1}].index must be {i}")
        if not (c["end_sec"] > c["start_sec"]):
            raise ValueError(f"clips[{i-1}] end_sec must be > start_sec")
        # Allow adjacency: start may equal previous end; disallow overlap
        if c["start_sec"] < prev_end:
            raise ValueError(f"clips[{i-1}] start_sec must be >= previous end_sec")
        prev_end = c["end_sec"]

        # Mode alignment
        mode = c["mode"]
        verse = c.get("verse") or None
        if mode == "dialogue" and not c.get("dialogue_text"):
            raise ValueError(f"clips[{i-1}] mode=dialogue requires dialogue_text")
        if mode == "verse" and not verse:
            raise ValueError(f"clips[{i-1}] mode=verse requires verse")
        if mode == "both" and not (verse and c.get("dialogue_text")):
            raise ValueError(f"clips[{i-1}] mode=both requires verse and dialogue_text")

        # Speech length heuristic: warn if short, but do not fail MVP
        speech = (c.get("dialogue_text") or "")
        if verse:
            speech = (verse.get("text", "") + " " + speech).strip()
        if len(speech) < 90:
            print(f"WARNING: clips[{i-1}] speech is short ({len(speech)} chars) — continuing anyway.", file=sys.stderr)

    return data
