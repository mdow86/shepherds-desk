#!/usr/bin/env python3
"""
Generate images from plan.json via Automatic1111 (Stable Diffusion WebUI) API.

Defaults:
- API base: env SD_API or http://127.0.0.1:7860
- Output dir: paths.OUTPUTS / "images"
- Plan: paths.OUTPUTS / "plan.json"
"""
from __future__ import annotations

import argparse, base64, json, os, re, sys, time
from pathlib import Path
from typing import Any, Dict, List
import requests

# --------------------------- Project paths ----------------------------------
try:
    import paths  # if a shim exists alongside the script
except Exception:
    from helpers import paths  # real location under src/media_generator/helpers

PLAN_DEFAULT   = paths.OUTPUTS / "plan.json"
OUTDIR_DEFAULT = paths.OUTPUTS / "images"
SD_API_DEFAULT = os.getenv("SD_API", "http://127.0.0.1:7860")

# --------------------------- Safety helpers ---------------------------------
NEGATIVE_PROMPT = (
    "nudity, nsfw, sexual, obscene, gore, graphic violence, blood, "
    "text, typography, watermark, logo, signature, "
    "low quality, blurry, deformed, extra fingers, extra limbs, mutated, disfigured, duplicated person, "
    "jpeg artifacts, anime, meme"
)
UNSAFE_PATTERNS = [r"\bnude\b", r"\bnudity\b", r"\bnsfw\b", r"\bgore\b", r"\bgraphic\b", r"\bviolence\b"]

def is_safe(prompt: str) -> bool:
    p = (prompt or "").lower()
    return not any(re.search(rx, p) for rx in UNSAFE_PATTERNS)

# ----------------------------- I/O helpers ----------------------------------
def load_plan(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"Missing plan: {path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON in plan: {e}", file=sys.stderr)
        sys.exit(1)

# --------------------------- Prompt shaping ---------------------------------
def build_prompt(base: str, style_suffix: str | None) -> str:
    base = (base or "").strip().rstrip(".")
    tail = "no text, no typography"
    if style_suffix:
        style = style_suffix.strip().rstrip(".")
        return f"{base}. {style}. {tail}"
    return f"{base}. {tail}"

# ------------------------- WebUI API client ---------------------------------
def sd_healthcheck(api_base: str, timeout: int = 10) -> None:
    try:
        r = requests.get(f"{api_base}/sdapi/v1/sd-models", timeout=timeout)
        r.raise_for_status()
    except Exception as e:
        print(f"SD WebUI API not reachable at {api_base}: {e}", file=sys.stderr)
        sys.exit(1)

def sd_txt2img(
    api_base: str,
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    sampler: str,
    seed: int = -1,
) -> bytes:
    payload = {
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "width": width,
        "height": height,
        "steps": steps,
        "cfg_scale": cfg,
        "sampler_name": sampler,
        "seed": seed,
        "restore_faces": False,
        "enable_hr": False,
        "save_images": False,
        "batch_size": 1,
        "n_iter": 1,
    }
    r = requests.post(f"{api_base}/sdapi/v1/txt2img", json=payload, timeout=300)
    r.raise_for_status()
    data = r.json()
    imgs = data.get("images", [])
    if not imgs:
        raise RuntimeError(f"No image returned: {data}")
    return base64.b64decode(imgs[0])

# --------------------------------- CLI --------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Generate clip images via SD WebUI API")
    ap.add_argument("--plan", type=Path, default=PLAN_DEFAULT)
    ap.add_argument("--outdir", type=Path, default=OUTDIR_DEFAULT)
    ap.add_argument("--w", type=int, default=1024)
    ap.add_argument("--h", type=int, default=576)
    ap.add_argument("--steps", type=int, default=28)
    ap.add_argument("--cfg", type=float, default=5.5)
    ap.add_argument("--sampler", type=str, default="DPM++ 2M Karras")
    ap.add_argument("--style", type=str, default="", help="Optional style suffix to append to prompts")
    return ap

# --------------------------------- Main -------------------------------------
def main() -> None:
    args = build_parser().parse_args()
    api_base = SD_API_DEFAULT

    if not args.plan.exists():
        print(f"Missing plan: {args.plan}", file=sys.stderr)
        sys.exit(1)
    args.outdir.mkdir(parents=True, exist_ok=True)

    sd_healthcheck(api_base)
    plan = load_plan(args.plan)
    clips: List[Dict[str, Any]] = plan.get("clips", [])
    if not clips:
        print("No clips in plan.", file=sys.stderr)
        sys.exit(1)

    style_suffix = args.style if args.style.strip() else None
    generated = 0
    total = len(clips)
    for clip in clips:
        idx = clip.get("index")
        base_prompt = clip.get("image_prompt", "")
        if not base_prompt:
            print(f"[clip{idx}] empty image_prompt; skipping", file=sys.stderr)
            continue
        if not is_safe(base_prompt):
            print(f"[clip{idx}] skipped due to unsafe prompt content")
            continue

        prompt = build_prompt(base_prompt, style_suffix)
        print(f"[{idx}/{total}] {prompt[:120]}...")
        try:
            png_bytes = sd_txt2img(
                api_base=api_base,
                prompt=prompt,
                negative_prompt=NEGATIVE_PROMPT,
                width=args.w,
                height=args.h,
                steps=args.steps,
                cfg=args.cfg,
                sampler=args.sampler,
            )
        except requests.HTTPError as e:
            print(f"[clip{idx}] HTTP error: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"[clip{idx}] generation failed: {e}", file=sys.stderr)
            sys.exit(1)

        out_path = args.outdir / f"clip{idx}.png"
        out_path.write_bytes(png_bytes)
        generated += 1
        time.sleep(0.1)

    print(f"Done. Wrote {generated} images → {args.outdir.resolve()}")

if __name__ == "__main__":
    main()
