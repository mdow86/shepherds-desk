from __future__ import annotations
import json, sys
from pathlib import Path
from typing import Any, Dict, List
from jsonschema import Draft7Validator

def load_schema(schema_path: Path) -> dict:
    with schema_path.open("r", encoding="utf-8") as f:
        return json.load(f)

def _synthesize_subtitle(c: Dict[str, Any]) -> str:
    verse = (c.get("verse") or {})
    vtext = (verse.get("text") or "").strip()
    dlg   = (c.get("dialogue_text") or "").strip()
    src = (dlg or vtext).strip()
    return (src[:100] if src else "")

def _repair_plan(data: Dict[str, Any]) -> Dict[str, Any]:
    clips: List[Dict[str, Any]] = data.get("clips") or []
    for c in clips:
        mode  = (c.get("mode") or "").strip()
        verse = c.get("verse") or None
        vtext = ((verse or {}).get("text") or "").strip()
        dlg   = (c.get("dialogue_text") or "").strip()
        sub   = (c.get("subtitle") or "").strip()

        # Demote invalid verse/both to dialogue if verse.text missing but dialogue present
        if mode in ("verse", "both"):
            if not verse or len(vtext) < 10:
                if dlg and len(dlg) >= 10:
                    c["mode"] = "dialogue"
                    c["verse"] = None

        # Fill subtitle if too short
        if len(sub) < 3:
            auto = _synthesize_subtitle(c)
            if len(auto) >= 3:
                c["subtitle"] = auto
    return data

def _validate_against_schema(data: dict, schema: dict) -> List[str]:
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: e.path)
    return [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in errors]

def parse_and_validate(raw_text: str, schema: dict) -> dict:
    # Parse
    s = raw_text.strip()
    try:
        data = json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e.msg} at pos {e.pos}") from e

    if not isinstance(data, dict) or "clips" not in data:
        raise ValueError("Schema validation failed: (root): object with 'clips' required")

    # First repair, then validate
    data = _repair_plan(data)
    errs = _validate_against_schema(data, schema)

    # If still failing due to empty subtitle/verse.text, try one more targeted repair
    if errs:
        changed = False
        for c in data.get("clips", []):
            if len((c.get("subtitle") or "").strip()) < 3:
                auto = _synthesize_subtitle(c)
                if len(auto) >= 3:
                    c["subtitle"] = auto
                    changed = True
            if (c.get("mode") in ("verse", "both")):
                verse = c.get("verse") or {}
                if len((verse.get("text") or "").strip()) < 10 and len((c.get("dialogue_text") or "").strip()) >= 10:
                    c["mode"] = "dialogue"
                    c["verse"] = None
                    changed = True
        if changed:
            errs = _validate_against_schema(data, schema)

    if errs:
        raise ValueError("Schema validation failed: " + " | ".join(errs))

    # Temporal + mode checks (keep strict)
    clips = data["clips"]
    prev_end = -1.0
    for i, c in enumerate(clips, start=1):
        if c["index"] != i:
            raise ValueError(f"clips[{i-1}].index must be {i}")
        if not (c["end_sec"] > c["start_sec"]):
            raise ValueError(f"clips[{i-1}] end_sec must be > start_sec")
        if c["start_sec"] < prev_end:
            raise ValueError(f"clips[{i-1}] start_sec must be >= previous end_sec")
        prev_end = c["end_sec"]

        mode = c["mode"]
        verse = c.get("verse") or None
        if mode == "dialogue" and not c.get("dialogue_text"):
            raise ValueError(f"clips[{i-1}] mode=dialogue requires dialogue_text")
        if mode == "verse" and not verse:
            raise ValueError(f"clips[{i-1}] mode=verse requires verse")
        if mode == "both" and not (verse and c.get("dialogue_text")):
            raise ValueError(f"clips[{i-1}] mode=both requires verse and dialogue_text")

        speech = (c.get("dialogue_text") or "")
        if verse:
            speech = (verse.get("text", "") + " " + speech).strip()
        if len(speech) < 90:
            print(f"WARNING: clips[{i-1}] speech is short ({len(speech)} chars) — continuing anyway.", file=sys.stderr)

    return data
