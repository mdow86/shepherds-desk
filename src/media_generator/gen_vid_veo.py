#!/usr/bin/env python3
from __future__ import annotations

import argparse, json, os, sys, time, re
from io import BytesIO
from pathlib import Path
from typing import Dict, Any, Optional

import requests
from dotenv import load_dotenv, find_dotenv
from PIL import Image
from google import genai
from google.genai import types

try:
    import paths
except Exception:
    from helpers import paths

PLAN_DEFAULT     = paths.OUTPUTS / "plan.json"
IMGDIR_DEFAULT   = paths.OUTPUTS / "images"
OUTDIR_DEFAULT   = paths.OUTPUTS / "video_veo"
STATE_DEFAULT    = paths.OUTPUTS / "veo_state.json"
MODEL_DEFAULT    = "veo-3.0-generate-001"  # try fast if needed: veo-3.0-fast-generate-001

STYLE_PRESETS = {
    "storybook": {
        "hint": "children's storybook look, gentle painterly shading, soft warm lighting, friendly expressive figures",
        "negative": "photorealistic, hyperrealism, harsh shadows, neon colors, cinematic realism, glitch, horror"
    },
    "oil": {
        "hint": "classic oil painting look on canvas, rich brushwork, renaissance palette, soft warm lighting",
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

def read_plan(p: Path) -> Dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Invalid or missing plan.json: {e}", file=sys.stderr); sys.exit(1)

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
    # Poll with capped retries
    tries = 0
    while True:
        if getattr(op, "done", False):
            return op
        time.sleep(8)
        op = client.operations.get(op)
        tries += 1
        if tries > 120:  # ~16 min max
            raise TimeoutError("Veo operation timed out")

def extract_video_handles(op) -> dict:
    """
    Return a dict with either:
      {"blob": <byteslike or file handle-ish>, "via": "sdk"}
    or
      {"uri": <download url string>, "via": "uri"}
    or raise with helpful diagnostics.
    """
    # 1) Preferred SDK shape
    resp = getattr(op, "response", None)
    if resp is not None:
        videos = getattr(resp, "generated_videos", None) or getattr(resp, "videos", None)
        if videos:
            return {"blob": videos[0].video, "via": "sdk"}

        # 2) Alternate REST-like shape (docs)
        gvr = getattr(resp, "generate_video_response", None) or getattr(resp, "generateVideoResponse", None)
        if gvr:
            samples = getattr(gvr, "generated_samples", None) or getattr(gvr, "generatedSamples", None) or []
            if samples:
                vid = getattr(samples[0], "video", None)
                uri = getattr(vid, "uri", None) if vid else None
                if uri:
                    return {"uri": uri, "via": "uri"}

    # 3) Error branch
    err = getattr(op, "error", None)
    if err:
        # Log full op for debugging
        try:
            dump = json.dumps(op.to_dict(), indent=2)
        except Exception:
            dump = str(op)
        raise RuntimeError(f"Veo operation error: {err}\n{dump}")

    # 4) Nothing usable
    try:
        dump = json.dumps(op.to_dict(), indent=2)
    except Exception:
        dump = str(op)
    raise RuntimeError(f"No videos in response; raw op:\n{dump}")

def download_uri_to_file(uri: str, out_path: Path, api_key: str) -> None:
    # Per docs, the URI requires API key header to follow redirects. :contentReference[oaicite:1]{index=1}
    with requests.get(uri, headers={"x-goog-api-key": api_key}, stream=True, allow_redirects=True) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk:
                    f.write(chunk)

def main() -> None:
    ap = argparse.ArgumentParser(description="Generate per-clip videos via Veo 3 (image→video)")
    ap.add_argument("--plan",     type=Path, default=PLAN_DEFAULT)
    ap.add_argument("--imgdir",   type=Path, default=IMGDIR_DEFAULT)
    ap.add_argument("--outdir",   type=Path, default=OUTDIR_DEFAULT)
    ap.add_argument("--model",    type=str,  default=MODEL_DEFAULT)
    ap.add_argument("--aspect",   type=str,  default="16:9", choices=["16:9", "9:16"])
    #ap.add_argument("--resolution", type=str, default="1080p", choices=["720p", "1080p"])
    ap.add_argument("--person-generation", type=str, default="", choices=["", "allow_all", "allow_adult", "dont_allow"])
    ap.add_argument("--negative", type=str,  default="", help="Override negativePrompt")
    ap.add_argument("--style-preset", type=str, choices=["storybook", "oil", "photo"], default="storybook")
    ap.add_argument("--max-per-day", type=int, default=0)
    ap.add_argument("--state-file", type=Path, default=STATE_DEFAULT)
    ap.add_argument("--api-key", type=str, default="")
    args = ap.parse_args()

    load_dotenv(find_dotenv())
    key = args.api_key or os.getenv("VEO_API_KEY")
    if not key:
        print("Missing VEO_API_KEY/GOOGLE_API_KEY", file=sys.stderr); sys.exit(1)

    if not args.plan.exists(): print(f"Missing plan: {args.plan}", file=sys.stderr); sys.exit(1)
    if not args.imgdir.exists(): print(f"Missing image dir: {args.imgdir}", file=sys.stderr); sys.exit(1)
    args.outdir.mkdir(parents=True, exist_ok=True)

    plan = read_plan(args.plan)
    clips = plan.get("clips", [])
    if not clips: print("No clips in plan.", file=sys.stderr); sys.exit(1)

    preset = STYLE_PRESETS[args.style_preset]
    style_hint = preset["hint"]
    style_neg  = preset["negative"]
    neg_all = args.negative.strip() or f"{BASE_NEGATIVE}, {style_neg}"

    client = genai.Client(api_key=key)

    total = len(clips)
    for i, clip in enumerate(clips, start=1):
        idx = int(clip.get("index", i) or i)
        img_path = args.imgdir / f"clip{idx}.png"
        if not img_path.exists():
            print(f"[{idx}/{total}] missing image: {img_path}", file=sys.stderr); sys.exit(1)

        prompt = derive_prompt(clip, style_hint)
        image_bytes = load_png_bytes(img_path)

        config = types.GenerateVideosConfig(
            aspect_ratio=args.aspect,
            #resolution=args.resolution, #veo 3 fast doesn't support resolution arg
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
            print(f"[{idx}] API error: {e}", file=sys.stderr); sys.exit(1)

        # Poll until complete
        try:
            op = poll_operation(client, op)
        except Exception as e:
            print(f"[{idx}] polling failed: {e}", file=sys.stderr); sys.exit(1)

        # Extract handles
        try:
            handle = extract_video_handles(op)
        except Exception as e:
            # If safety/audio blocked, show concise reason if present
            m = str(e)
            if re.search(r"(blocked|safety|audio)", m, re.I):
                print(f"[{idx}] generation blocked: {e}", file=sys.stderr)
            else:
                print(f"[{idx}] response parse error: {e}", file=sys.stderr)
            sys.exit(1)

        out_path = args.outdir / f"clip{idx}.mp4"
        # Download/save
        try:
            if handle.get("via") == "sdk":
                # SDK object exposes bytes or a file handle
                blob = handle["blob"]
                try:
                    # Preferred in recent SDKs
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
            print(f"[{idx}] download failed: {e}", file=sys.stderr)
            sys.exit(1)

        # increment lightweight counter
        try:
            from datetime import datetime, timezone
            state = {}
            if args.state_file.exists():
                state = json.loads(args.state_file.read_text(encoding="utf-8"))
            today = datetime.now(timezone.utc).date().isoformat()
            counts = state.get("counts", {})
            counts[today] = int(counts.get(today, 0)) + 1
            state["counts"] = counts
            args.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception:
            pass

        print(f"[{idx}/{total}] wrote {out_path.name}")

    print(f"Done. Wrote {total} file(s) → {args.outdir.resolve()}")

if __name__ == "__main__":
    main()
