#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys, time, re, os
from pathlib import Path
from io import BytesIO
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple

from dotenv import load_dotenv, find_dotenv
from google import genai
from google.genai import types
from PIL import Image, ImageFilter
import requests

try:
    import paths
except Exception:
    from helpers import paths

DEFAULT_PLAN   = paths.OUTPUTS / "plan.json"
DEFAULT_OUTDIR = paths.OUTPUTS / "images"
DEFAULT_MODEL  = "gemini-2.5-flash-image-preview"
DEFAULT_STATE  = paths.OUTPUTS / "gemini_state.json"
REMOTE_MAP     = paths.OUTPUTS / "remote_map.json"

# -------- style presets --------
STYLE_PRESETS = {
    "storybook": {
        "style": (
            "Children's storybook illustration, soft warm lighting, gentle painterly shading, "
            "smooth clean outlines, natural outdoor setting, rays of sunlight, pastel and earth-tone colors, "
            "balanced composition, friendly expressive characters, uplifting peaceful mood, high detail, digital painting"
        ),
        "negative": "photorealistic, hyperrealism, harsh shadows, neon colors, cinematic realism, glitch, horror"
    },
    "oil": {
        "style": (
            "classic oil painting on canvas, rich impasto brushwork, soft warm lighting, renaissance palette, "
            "subtle varnish sheen, painterly texture, museum-grade artwork"
        ),
        "negative": "cartoon, line art, neon colors, glitch, anime, horror, text"
    },
    "photo": {
        "style": (
            "photorealistic, natural lighting, cinematic depth of field, realistic textures, accurate anatomy, "
            "clean color grading"
        ),
        "negative": "cartoon, illustration, oil painting, line art, neon colors, glitch, horror, text"
    },
}

# -------- safety negatives (always applied) --------
BASE_NEG = (
    "nudity, suggestive, revealing clothing, kissing, violence, blood, gore, weapons, torture, "
    "horror, occult, text, watermark, logo, low quality, distortion"
)

SANITIZE_MAP = {
    r"\bwomb\b": "abdomen",
    r"\bpregnan(t|cy)\b": "expectant mother",
    r"\bnaked\b": "modest attire",
    r"\bkiss(ing)?\b": "respectful distance",
    r"\bblood(y)?\b": "clean",
    r"\bgore\b": "clean",
    r"\bviolence\b": "peaceful",
    r"\bbrutal\b": "gentle",
    r"\boccult\b": "absent",
}
UNSAFE_PATTERNS = [
    r"\bnude\b", r"\bnudity\b", r"\bnsfw\b", r"\bgore\b", r"\bgraphic\b",
    r"\bviolence\b", r"\bweapon(s)?\b", r"\bkill\b", r"\bsex\b"
]

def utc_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()

def is_safe_text(s: str) -> bool:
    p = (s or "").lower()
    return not any(re.search(rx, p) for rx in UNSAFE_PATTERNS)

def sanitize_prompt(p: str) -> str:
    s = p or ""
    for rx, repl in SANITIZE_MAP.items():
        s = re.sub(rx, repl, s, flags=re.IGNORECASE)
    return s

def build_prompt(base: str, style_suffix: str, style_neg: str) -> str:
    base = (base or "").strip().rstrip(".")
    style = (style_suffix or "").strip().rstrip(".")
    parts = [base, style, "16:9 framing implied", f"{BASE_NEG}, {style_neg}"]
    return ". ".join([p for p in parts if p])

def load_plan(plan_path: Path) -> Dict[str, Any]:
    return json.loads(plan_path.read_text(encoding="utf-8"))

def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists(): return {}
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception: return {}

def save_state(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

def check_daily_cap(state_path: Path, cap: int) -> None:
    if cap <= 0: return
    state = load_state(state_path)
    n = int(state.get("counts", {}).get(utc_date(), 0))
    if n >= cap:
        print(f"[GEMINI] daily cap reached: {n}/{cap}", file=sys.stderr); sys.exit(3)

def incr_daily(state_path: Path) -> None:
    state = load_state(state_path)
    counts = state.get("counts", {})
    counts[utc_date()] = int(counts.get(utc_date(), 0)) + 1
    state["counts"] = counts
    save_state(state_path, state)

def make_placeholder(size=(1280, 720)) -> Image.Image:
    w, h = size
    img = Image.new("RGB", (w, h))
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(235 - 20 * t); g = int(240 - 30 * t); b = int(250 - 60 * t)
        for x in range(w):
            img.putpixel((x, y), (r, g, b))
    return img.filter(ImageFilter.GaussianBlur(radius=3))

def try_generate_image(client: genai.Client, model: str, prompt: str, negative: str) -> Optional[bytes]:
    try:
        resp = client.models.generate_images(model=model, prompt=prompt, negative_prompt=negative)
        if getattr(resp, "images", None):
            im = resp.images[0]
            data = getattr(im, "image_bytes", None) or getattr(im, "data", None)
            if data: return data
    except Exception:
        pass
    try:
        resp = client.models.generate_content(model=model, contents=[prompt])
        cand = getattr(resp, "candidates", None) or []
        if cand:
            parts = getattr(cand[0].content, "parts", []) or []
            for part in parts:
                inline = getattr(part, "inline_data", None)
                if inline and getattr(inline, "data", None):
                    return inline.data
    except Exception:
        pass
    return None

# ---------------- Supabase upload ----------------
def _sb_urls() -> Tuple[str, str]:
    base = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    if not base:
        print("Missing SUPABASE_URL in env", file=sys.stderr); sys.exit(2)
    return base, f"{base}/storage/v1"

def upload_to_supabase_png(bucket: str, object_path: str, data: bytes, upsert: bool = True) -> str:
    base, api = _sb_urls()
    svc = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_ANON_KEY") or ""
    if not svc:
        print("Missing SUPABASE_SERVICE_KEY (preferred) or SUPABASE_ANON_KEY", file=sys.stderr); sys.exit(2)
    url = f"{api}/object/{bucket}/{object_path.strip('/')}"
    headers = {
        "Authorization": f"Bearer {svc}",
        "apikey": svc,                     # required by Supabase edge
        "Content-Type": "image/png",
        "x-upsert": "true" if upsert else "false",
    }
    r = requests.post(url, headers=headers, data=data, timeout=60)
    if r.status_code not in (200, 201):
        print(f"[SUPABASE] upload failed {r.status_code}: {r.text[:300]}", file=sys.stderr); sys.exit(2)
    return f"{api}/object/public/{bucket}/{object_path.strip('/')}"

def main() -> None:
    ap = argparse.ArgumentParser(description="Generate images via Gemini and save to Supabase or local")
    ap.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    ap.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    ap.add_argument("--model", type=str, default=DEFAULT_MODEL)
    ap.add_argument("--style-preset", type=str, choices=list(STYLE_PRESETS.keys()), default="storybook")
    ap.add_argument("--state-file", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--max-per-day", type=int, default=0)
    ap.add_argument("--api-key", type=str, default="")

    # new controls
    ap.add_argument("--save-dest", choices=["supabase","local"], default="supabase")
    ap.add_argument("--sb-bucket", type=str, default=os.getenv("SUPABASE_BUCKET") or "SUPABASE_BUCKET")
    ap.add_argument("--sb-prefix", type=str, default=os.getenv("JOB_PREFIX") or "jobs/manual-run")
    ap.add_argument("--also-save-local", action="store_true")

    args = ap.parse_args()
    load_dotenv(find_dotenv())

    key = args.api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        print("Missing GEMINI_API_KEY/GOOGLE_API_KEY", file=sys.stderr); sys.exit(1)

    if not args.plan.exists(): print(f"Missing plan: {args.plan}", file=sys.stderr); sys.exit(1)
    args.outdir.mkdir(parents=True, exist_ok=True)

    plan = load_plan(args.plan)
    clips = plan.get("clips", [])
    if not clips: print("No clips in plan.", file=sys.stderr); sys.exit(1)

    preset = STYLE_PRESETS[args.style_preset]
    style_suffix, style_neg = preset["style"], preset["negative"]

    client = genai.Client(api_key=key)
    print(f"Model: {args.model} | style={args.style_preset} | save={args.save_dest}")

    # load existing remote map to preserve any prior entries
    if REMOTE_MAP.exists():
        try:
            remote_map: Dict[str,str] = json.loads(REMOTE_MAP.read_text(encoding="utf-8"))
        except Exception:
            remote_map = {}
    else:
        remote_map = {}

    generated = 0
    total = len(clips)
    for clip in clips:
        idx = clip.get("index")
        base_prompt = clip.get("image_prompt") or ""
        if not base_prompt:
            print(f"[clip{idx}] empty image_prompt; skipping", file=sys.stderr)
            continue

        if not is_safe_text(base_prompt):
            print(f"[clip{idx}] prompt contained unsafe terms; auto-sanitizing", file=sys.stderr)
        safe_prompt = sanitize_prompt(base_prompt)

        check_daily_cap(args.state_file, args.max_per_day)

        prompt1 = build_prompt(safe_prompt, style_suffix, style_neg)
        print(f"[{idx}/{total}] {prompt1[:120]}...")
        img_bytes = try_generate_image(client, args.model, prompt1, f"{BASE_NEG}, {style_neg}")

        if not img_bytes:
            softened = re.sub(r"\b(hands?|faces?|bodies?|abdomen|mother|child|baby)\b", "figure", safe_prompt, flags=re.I)
            softened = re.sub(r"\b(close[- ]?up|portrait)\b", "wide shot", softened, flags=re.I)
            prompt2 = build_prompt(
                f"{softened}. Focus on environment and composition; modest attire; respectful distance",
                style_suffix, style_neg
            )
            print(f"[clip{idx}] retry with softened prompt")
            img_bytes = try_generate_image(client, args.model, prompt2, f"{BASE_NEG}, {style_neg}")

        if not img_bytes:
            scene_only = "Peaceful village morning, humble home, soft sunrise light, gentle breeze"
            prompt3 = build_prompt(scene_only, style_suffix, style_neg)
            print(f"[clip{idx}] retry with scene-only fallback")
            img_bytes = try_generate_image(client, args.model, prompt3, f"{BASE_NEG}, {style_neg}")

        local_path = args.outdir / f"clip{idx}.png"
        object_path = f"{args.sb_prefix.strip('/')}/clip{idx}.png"

        if img_bytes:
            if args.save_dest == "supabase":
                public_url = upload_to_supabase_png(args.sb_bucket, object_path, img_bytes, upsert=True)
                remote_map[str(idx)] = public_url
                if args.also_save_local:
                    try:
                        Image.open(BytesIO(img_bytes)).save(local_path)
                    except Exception:
                        local_path.write_bytes(img_bytes)
            else:
                try:
                    Image.open(BytesIO(img_bytes)).save(local_path)
                except Exception:
                    local_path.write_bytes(img_bytes)
                base, api = _sb_urls()
                remote_map[str(idx)] = f"{api}/object/public/{args.sb_bucket}/{object_path}"

            incr_daily(args.state_file)
            generated += 1
            time.sleep(0.15)
            continue

        make_placeholder().save(local_path, format="PNG")

    REMOTE_MAP.parent.mkdir(parents=True, exist_ok=True)
    REMOTE_MAP.write_text(json.dumps(remote_map, indent=2), encoding="utf-8")
    print(f"Updated {REMOTE_MAP}")

    print(f"Done. Wrote {generated} generated image(s). Destination={args.save_dest}.")
    if args.save_dest == "supabase":
        print(f"Bucket={args.sb_bucket} Prefix={args.sb_prefix}")
        print("Ensure bucket is Public and anon SELECT policy exists.")
    else:
        print(f"Local outdir: {args.outdir.resolve()}")

if __name__ == "__main__":
    main()
