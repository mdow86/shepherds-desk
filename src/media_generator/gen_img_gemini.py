#!/usr/bin/env python3
"""
Generate images for each clip in plan.json using Gemini.

Prereq:
  pip install -U google-genai python-dotenv pillow

Auth:
  Put GEMINI_API_KEY=... or GOOGLE_API_KEY=... in .env (repo root) or env.
"""
from __future__ import annotations

import argparse, json, sys, time, re, os
from pathlib import Path
from io import BytesIO
from datetime import datetime
from typing import Dict, Any

from dotenv import load_dotenv, find_dotenv
from google import genai
from PIL import Image

# project paths
try:
    import paths
except Exception:
    from helpers import paths

DEFAULT_PLAN   = paths.OUTPUTS / "plan.json"
DEFAULT_OUTDIR = paths.OUTPUTS / "images"
DEFAULT_MODEL  = "gemini-2.5-flash-image-preview"
DEFAULT_STATE  = paths.OUTPUTS / "gemini_state.json"

NEGATIVE_PROMPT_HINT = "no text or typography, no watermark, no logo"
UNSAFE_PATTERNS = [r"\bnude\b", r"\bnudity\b", r"\bnsfw\b", r"\bgore\b", r"\bgraphic\b", r"\bviolence\b"]

def is_safe(prompt: str) -> bool:
    p = (prompt or "").lower()
    return not any(re.search(rx, p) for rx in UNSAFE_PATTERNS)

def build_prompt(base: str, style_suffix: str | None) -> str:
    base = (base or "").strip().rstrip(".")
    tail = NEGATIVE_PROMPT_HINT
    if style_suffix:
        style_suffix = style_suffix.strip().rstrip(".")
        return f"{base}. {style_suffix}. {tail}"
    return f"{base}. {tail}"

def load_plan(plan_path: Path) -> Dict[str, Any]:
    with plan_path.open("r", encoding="utf-8") as f:
        return json.load(f)

def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_state(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

def check_daily_cap(state_path: Path, cap: int) -> None:
    if cap <= 0:
        return
    state = load_state(state_path)
    today = datetime.utcnow().date().isoformat()
    n = int(state.get("counts", {}).get(today, 0))
    if n >= cap:
        print(f"[GEMINI] daily cap reached: {n}/{cap}", file=sys.stderr)
        sys.exit(3)

def incr_daily(state_path: Path) -> None:
    state = load_state(state_path)
    today = datetime.utcnow().date().isoformat()
    counts = state.get("counts", {})
    counts[today] = int(counts.get(today, 0)) + 1
    state["counts"] = counts
    save_state(state_path, state)

def main() -> None:
    ap = argparse.ArgumentParser(description="Generate images via Gemini 2.5 Flash Image")
    ap.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    ap.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    ap.add_argument("--model", type=str, default=DEFAULT_MODEL)
    ap.add_argument("--style", type=str, default="", help="Optional style suffix")
    ap.add_argument("--state-file", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--max-per-day", type=int, default=0)
    ap.add_argument("--api-key", type=str, default="", help="Override API key (GEMINI_API_KEY/GOOGLE_API_KEY)")
    args = ap.parse_args()

    load_dotenv(find_dotenv())
    key = args.api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        print("Missing GEMINI_API_KEY/GOOGLE_API_KEY in environment or .env", file=sys.stderr)
        sys.exit(1)

    if not args.plan.exists():
        print(f"Missing plan: {args.plan}", file=sys.stderr)
        sys.exit(1)
    args.outdir.mkdir(parents=True, exist_ok=True)

    plan = load_plan(args.plan)
    clips = plan.get("clips", [])
    if not clips:
        print("No clips in plan.", file=sys.stderr)
        sys.exit(1)

    style_suffix = args.style.strip() or None
    client = genai.Client(api_key=key)
    print(f"Model: {args.model}")

    generated = 0
    total = len(clips)
    for clip in clips:
        idx = clip.get("index")
        base_prompt = clip.get("image_prompt") or ""
        if not base_prompt:
            print(f"[clip{idx}] empty image_prompt; skipping", file=sys.stderr)
            continue
        if not is_safe(base_prompt):
            print(f"[clip{idx}] skipped due to unsafe prompt content")
            continue

        check_daily_cap(args.state_file, args.max_per_day)
        prompt = build_prompt(base_prompt, style_suffix)
        print(f"[{idx}/{total}] {prompt[:120]}...")

        try:
            resp = client.models.generate_content(model=args.model, contents=[prompt])
        except Exception as e:
            print(f"[clip{idx}] API error: {e}", file=sys.stderr)
            sys.exit(1)

        img_bytes = None
        cand = getattr(resp, "candidates", None) or []
        if cand:
            parts = getattr(cand[0].content, "parts", []) or []
            for part in parts:
                inline = getattr(part, "inline_data", None)
                if inline and getattr(inline, "data", None):
                    img_bytes = inline.data
                    break
        if not img_bytes:
            print(f"[clip{idx}] no image returned", file=sys.stderr)
            sys.exit(1)

        out_path = args.outdir / f"clip{idx}.png"
        try:
            Image.open(BytesIO(img_bytes)).save(out_path)
        except Exception:
            out_path.write_bytes(img_bytes)

        incr_daily(args.state_file)
        generated += 1
        time.sleep(0.15)

    print(f"Done. Wrote {generated} images → {args.outdir.resolve()}")

if __name__ == "__main__":
    main()
