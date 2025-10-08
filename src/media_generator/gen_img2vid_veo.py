#!/usr/bin/env python3
from __future__ import annotations

import argparse, json, os, sys, time, re
from io import BytesIO
from pathlib import Path
from typing import Dict, Any

import requests
from dotenv import load_dotenv, find_dotenv
from PIL import Image

# Google Gemini (Veo) SDK
from google import genai
from google.genai import types

try:
    import paths
except Exception:
    from helpers import paths

PLAN_DEFAULT   = paths.OUTPUTS / "plan.json"
IMGDIR_DEFAULT = paths.OUTPUTS / "images"
OUTDIR_DEFAULT = paths.OUTPUTS / "video_veo"
STATE_DEFAULT  = paths.OUTPUTS / "veo_state.json"

MODEL_DEFAULT  = "veo-3.0-fast-generate-001"  # low-cost variant
ASPECTS        = ("16:9", "9:16")

STYLE_PRESETS = {
    "storybook": {
        "hint": "children's storybook look, gentle painterly shading, soft warm lighting, friendly expressive figures",
        "negative": "photorealistic, hyperrealism, harsh shadows, neon colors, cinematic realism, glitch, horror"
    },
    "oil": {
        "hint": "classic oil painting on canvas, rich brushwork, renaissance palette, soft warm lighting",
        "negative": "cartoon, anime, line art, neon colors, glitch, horror"
    },
    "photo": {
        "hint": "photorealistic natural cinematography, realistic textures, clean color grading, shallow depth of field",
        "negative": "cartoon, illustration, painterly, neon colors, glitch, horror"
    },
}

BASE_NEGATIVE = (
    "nudity, suggestive, revealing clothing, kissing, violence, blood, gore, weapons, torture, "
    "horror, occult, text, watermark, logo, low quality, distortion"
)

AUDIO_POLICY = (
    "No dialogue, no lyrics. Subtle ambient sound only that fits the scene "
    "(soft wind, quiet footsteps, gentle fire crackle, room tone)."
)

def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr); sys.exit(code)

def read_plan(p: Path) -> Dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        die(f"Invalid or missing plan.json: {e}")

def load_png_bytes(img_path: Path) -> bytes:
    b = img_path.read_bytes()
    try:
        im = Image.open(BytesIO(b)).convert("RGBA")
        bio = BytesIO(); im.save(bio, format="PNG")
        return bio.getvalue()
    except Exception:
        return b

def derive_prompt(clip: Dict[str, Any], style_hint: str) -> str:
    base = (clip.get("image_prompt") or "").strip().rstrip(".")
    motion = (clip.get("video_motion_prompt") or "").strip().rstrip(".")
    parts = [p for p in (base, motion, style_hint) if p]
    parts += ["16:9 framing implied", AUDIO_POLICY]
    return ". ".join(parts)

def poll_operation(client: genai.Client, op) -> any:
    tries = 0
    while True:
        if getattr(op, "done", False):
            return op
        time.sleep(8)
        op = client.operations.get(op)
        tries += 1
        if tries > 120:
            raise TimeoutError("Veo operation timed out")

def extract_video_handles(op) -> dict:
    resp = getattr(op, "response", None)
    if resp is not None:
        videos = getattr(resp, "generated_videos", None) or getattr(resp, "videos", None)
        if videos:
            return {"blob": videos[0].video, "via": "sdk"}
        gvr = getattr(resp, "generate_video_response", None) or getattr(resp, "generateVideoResponse", None)
        if gvr:
            samples = getattr(gvr, "generated_samples", None) or getattr(gvr, "generatedSamples", None) or []
            if samples:
                vid = getattr(samples[0], "video", None)
                uri = getattr(vid, "uri", None) if vid else None
                if uri:
                    return {"uri": uri, "via": "uri"}

    err = getattr(op, "error", None)
    if err:
        try:
            dump = json.dumps(op.to_dict(), indent=2)
        except Exception:
            dump = str(op)
        raise RuntimeError(f"Veo operation error: {err}\n{dump}")

    try:
        dump = json.dumps(op.to_dict(), indent=2)
    except Exception:
        dump = str(op)
    raise RuntimeError(f"No videos in response; raw op:\n{dump}")

def download_uri_to_file(uri: str, out_path: Path, api_key: str) -> None:
    with requests.get(uri, headers={"x-goog-api-key": api_key}, stream=True, allow_redirects=True) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk: f.write(chunk)

def main() -> None:
    ap = argparse.ArgumentParser(description="Image→Video with Veo 3 Fast (img2vid). Writes clip{N}.mp4 into video_veo/")
    ap.add_argument("--plan",     type=Path, default=PLAN_DEFAULT)
    ap.add_argument("--imgdir",   type=Path, default=IMGDIR_DEFAULT)
    ap.add_argument("--outdir",   type=Path, default=OUTDIR_DEFAULT)
    ap.add_argument("--model",    type=str,  default=MODEL_DEFAULT)
    ap.add_argument("--aspect",   type=str,  default="16:9", choices=list(ASPECTS))
    ap.add_argument("--person-generation", type=str, default="", choices=["", "allow_all", "allow_adult", "dont_allow"])
    ap.add_argument("--negative", type=str,  default="", help="Override negativePrompt")
    ap.add_argument("--style-preset", type=str, choices=list(STYLE_PRESETS.keys()), default="storybook")
    ap.add_argument("--max-per-day", type=int, default=0)
    ap.add_argument("--state-file", type=Path, default=STATE_DEFAULT)
    ap.add_argument("--api-key", type=str, default="")
    ap.add_argument("--clips", type=int, default=0, help="If >0 limit to first N clips")
    args = ap.parse_args()

    load_dotenv(find_dotenv())
    key = args.api_key or os.getenv("VEO_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not key:
        die("Missing API key: set VEO_API_KEY or pass --api-key")

    if not args.plan.exists(): die(f"Missing plan: {args.plan}")
    if not args.imgdir.exists(): die(f"Missing image dir: {args.imgdir}")
    args.outdir.mkdir(parents=True, exist_ok=True)

    plan = read_plan(args.plan)
    clips = plan.get("clips", [])
    if not clips: die("No clips in plan.")
    if args.clips and args.clips > 0:
        clips = clips[: args.clips]

    preset = STYLE_PRESETS[args.style_preset]
    style_hint = preset["hint"]
    style_neg  = preset["negative"]
    neg_all = args.negative.strip() or f"{BASE_NEGATIVE}, {style_neg}"

    # simple daily limiter
    if args.max_per_day and args.state_file:
        from datetime import datetime, timezone
        state = {}
        if args.state_file.exists():
            state = json.loads(args.state_file.read_text(encoding="utf-8"))
        today = datetime.now(timezone.utc).date().isoformat()
        if int(state.get("counts", {}).get(today, 0)) + len(clips) > args.max_per_day:
            die(f"max-per-day exceeded: {args.max_per_day}")
        # write after success later

    client = genai.Client(api_key=key)

    total = len(clips)
    for i, clip in enumerate(clips, start=1):
        idx = int(clip.get("index", i) or i)
        img_path = args.imgdir / f"clip{idx}.png"
        if not img_path.exists():
            die(f"[{idx}/{total}] missing image: {img_path}")

        prompt = derive_prompt(clip, style_hint)
        image_bytes = load_png_bytes(img_path)

        config = types.GenerateVideosConfig(
            aspect_ratio=args.aspect,
            negative_prompt=neg_all,
        )
        if args.person_generation:
            setattr(config, "person_generation", args.person_generation)

        print(f"[{idx}/{total}] Veo request…")
        try:
            op = client.models.generate_videos(
                model=args.model,
                prompt=prompt,
                image=types.Image(image_bytes=image_bytes, mime_type="image/png"),
                config=config,
            )
        except Exception as e:
            die(f"[{idx}] API error: {e}")

        try:
            op = poll_operation(client, op)
        except Exception as e:
            die(f"[{idx}] polling failed: {e}")

        try:
            handle = extract_video_handles(op)
        except Exception as e:
            m = str(e)
            if re.search(r"(blocked|safety|audio)", m, re.I):
                die(f"[{idx}] generation blocked: {e}")
            die(f"[{idx}] response parse error: {e}")

        out_path = args.outdir / f"clip{idx}.mp4"
        try:
            if handle.get("via") == "sdk":
                blob = handle["blob"]
                try:
                    client.files.download(file=blob)
                    blob.save(str(out_path))
                except Exception:
                    data = getattr(blob, "video_bytes", None)
                    if data:
                        Path(out_path).write_bytes(data)
                    else:
                        raise
            else:
                download_uri_to_file(handle["uri"], out_path, key)
        except Exception as e:
            die(f"[{idx}] download failed: {e}")

        print(f"[{idx}/{total}] wrote {out_path.name}")

    # persist daily count
    try:
        if args.max_per_day and args.state_file:
            from datetime import datetime, timezone
            state = {}
            if args.state_file.exists():
                state = json.loads(args.state_file.read_text(encoding="utf-8"))
            today = datetime.now(timezone.utc).date().isoformat()
            counts = state.get("counts", {})
            counts[today] = int(counts.get(today, 0)) + total
            state["counts"] = counts
            args.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:
        pass

    print(f"Done. Wrote {total} file(s) → {args.outdir.resolve()}")

if __name__ == "__main__":
    main()
