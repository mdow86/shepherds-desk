#!/usr/bin/env python3
from __future__ import annotations

"""
Image→Video via VideoGenAPI with robust 429 backoff.

- Sequential per-clip submission (keeps concurrency low).
- Exponential backoff on 429 and 5xx for /generate and /status.
- Respects Retry-After when provided.
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests
from dotenv import load_dotenv

# ---------------- paths shim ----------------
try:
    import paths
except Exception:
    from helpers import paths  # type: ignore

PLAN_DEFAULT   = paths.OUTPUTS / "plan.json"
IMGDIR_DEFAULT = paths.OUTPUTS / "images"
OUTDIR_DEFAULT = paths.OUTPUTS / "video_veo"
MAP_PATH       = paths.OUTPUTS / "remote_map.json"

# --------------- robust .env loading ---------------
def _load_env() -> None:
    candidates = [
        (Path(paths.CODE_ROOT).parent.parent / ".env") if hasattr(paths, "CODE_ROOT") else None,
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[2] / ".env",
        Path(__file__).resolve().parents[1] / ".env",
    ]
    for p in [c for c in candidates if c]:
        if p.exists():
            load_dotenv(p, override=False)
            break
    load_dotenv(override=False)

_load_env()

# ---------- API base + key ----------
def _normalize_base(raw: Optional[str]) -> str:
    if not raw or not raw.strip():
        return "https://videogenapi.com/api/v1"
    b = raw.strip().rstrip("/")
    # Accept: https://videogenapi.com, /api, /api/v1
    if b.endswith("/api"):
        b = b + "/v1"
    elif not b.endswith("/api/v1"):
        if b.endswith("/api/v2") or b.endswith("/api/v3"):
            pass
        elif b.endswith("/api"):
            b = b + "/v1"
        elif "/api/" not in b:
            b = b + "/api/v1"
    return b

API_BASE = _normalize_base(os.getenv("HIGGS_BASE_URL") or os.getenv("VIDEOGEN_BASE_URL"))
API_KEY = (
    os.getenv("HIGGS_VID_API_KEY")  # preferred
    or os.getenv("VIDEOGEN_API_KEY")
    or os.getenv("HIGGS_VEO_API_KEY")
    or ""
)

GENERATE_ENDPOINT_DEFAULT = f"{API_BASE}/generate"
STATUS_BASE_DEFAULT       = f"{API_BASE}/status"

MAX_SEC  = 10
DEF_SEC  = 8
DEF_RES  = "720p"
DEF_MODEL = "kling_25"

# ---------- helpers ----------
def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    sys.exit(code)

def read_plan(p: Path) -> Dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        die(f"Invalid or missing plan.json: {e}")
        return {}

def load_remote_map(map_path: Path) -> Dict[str, str]:
    if not map_path.exists():
        return {}
    try:
        return json.loads(map_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

_URL_RE = re.compile(r"^https?://", re.I)

def _clean_url(u: str) -> str:
    u = (u or "").strip().replace("\n", "").replace("\r", "")
    # collapse accidental multiple schemes (e.g., https://https://...)
    u = re.sub(r"^(https?://)+", r"\1", u, flags=re.I)
    return u

def local_or_remote_png(idx: int, imgdir: Path, remote_map: Dict[str, str]) -> Tuple[str, bool]:
    if str(idx) in remote_map:
        url = _clean_url(remote_map[str(idx)])
        if not _URL_RE.match(url):
            die(f"clip{idx}: invalid remote URL: {url}")
        return url, True

    p = imgdir / f"clip{idx}.png"
    if not p.exists():
        die(f"missing image: {p}")
    die(f"clip{idx}: no public URL found. Create signed/public URLs in {MAP_PATH.name}.")

def build_prompt(clip: Dict[str, Any], override_prompt: Optional[str]) -> str:
    if override_prompt:
        return override_prompt.strip()
    base = (clip.get("image_prompt") or "").strip().rstrip(".")
    motion = (clip.get("video_motion_prompt") or "").strip().rstrip(".")
    prompt = ". ".join(x for x in (base, motion) if x) or "Gentle camera move on scene. No overlays."
    return prompt

def _retry_after_seconds(resp: requests.Response, default_wait: float) -> float:
    ra = resp.headers.get("Retry-After")
    if not ra:
        return default_wait
    try:
        return max(default_wait, float(ra))
    except Exception:
        return default_wait

def _debug_dump(label: str, resp: requests.Response) -> None:
    ct = resp.headers.get("content-type", "") or ""
    body = resp.text if ("text" in ct or "json" in ct) else f"<{ct} {len(resp.content)} bytes>"
    print(f"[DEBUG] {label} status={resp.status_code} resp={body[:1500]}", file=sys.stderr)

# ---------- network with backoff ----------
def post_json(url: str, headers: dict, payload: dict, debug: bool) -> requests.Response:
    """
    POST with exponential backoff for 429/5xx.
    Total ~60–90s before giving up.
    """
    max_tries = 8
    backoff = 2.0
    for attempt in range(1, max_tries + 1):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=60)
        except Exception as e:
            if attempt == max_tries:
                die(f"HTTP error calling {url}: {e}")
            if debug:
                print(f"[DEBUG] POST exception: {e} (attempt {attempt})", file=sys.stderr)
            time.sleep(backoff)
            backoff *= 1.8
            continue

        if r.status_code == 429:
            wait = _retry_after_seconds(r, backoff)
            if debug:
                _debug_dump("POST 429", r)
                print(f"[DEBUG] 429; sleeping {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
            backoff *= 1.8
            continue

        if 500 <= r.status_code < 600:
            if attempt == max_tries:
                die(f"generate error: {r.status_code} {r.text[:800]}")
            if debug:
                _debug_dump("POST 5xx", r)
                print(f"[DEBUG] 5xx; sleeping {backoff:.1f}s", file=sys.stderr)
            time.sleep(backoff)
            backoff *= 1.8
            continue

        if debug:
            _debug_dump("POST OK", r)
        return r

    die("exhausted retries")

def get_json(url: str, headers: dict, debug: bool) -> dict:
    """
    GET with exponential backoff for 429/5xx.
    """
    max_tries = 10
    backoff = 2.0
    for attempt in range(1, max_tries + 1):
        try:
            r = requests.get(url, headers=headers, timeout=30)
        except Exception as e:
            if attempt == max_tries:
                die(f"HTTP error calling {url}: {e}")
            if debug:
                print(f"[DEBUG] GET exception: {e} (attempt {attempt})", file=sys.stderr)
            time.sleep(backoff)
            backoff *= 1.8
            continue

        if r.status_code == 429:
            wait = _retry_after_seconds(r, backoff)
            if debug:
                _debug_dump("GET 429", r)
                print(f"[DEBUG] 429; sleeping {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
            backoff *= 1.8
            continue

        if 500 <= r.status_code < 600:
            if attempt == max_tries:
                die(f"status 5xx: {r.status_code}\n{r.text[:800]}")
            if debug:
                _debug_dump("GET 5xx", r)
                print(f"[DEBUG] 5xx; sleeping {backoff:.1f}s", file=sys.stderr)
            time.sleep(backoff)
            backoff *= 1.8
            continue

        if r.status_code >= 400:
            die(f"status error: {r.status_code}\n{r.text[:800]}")

        try:
            return r.json()
        except Exception:
            die(f"Invalid JSON from status: {r.text[:500]}")

    die("exhausted retries")
    return {}

def poll_generation(status_base: str, generation_id: string, headers: dict, debug: bool,
                    min_interval: float = 4.0, max_wait: int = 900) -> dict:
    """
    Poll status with a floor interval to reduce pressure on the API.
    On 429, the inner get_json backoff applies; we still keep min_interval pacing.
    """
    url = f"{status_base.rstrip('/')}/{generation_id}"
    t0 = time.time()
    interval = min_interval
    while True:
        data = get_json(url, headers, debug)
        state = (data.get("status") or "").lower()
        if state in ("completed",):
            return data
        if state in ("failed", "error"):
            die(f"generation failed: {data}")

        if time.time() - t0 > max_wait:
            die("Timeout waiting for generation")

        # Light pacing. If service returns in_queue/in_progress, keep interval.
        # Optionally back off slightly after long waits.
        time.sleep(interval)
        if interval < 8.0:
            interval = min(8.0, interval + 0.5)

# ---------- main ----------
def main() -> None:
    ap = argparse.ArgumentParser(description="Image→Video via VideoGenAPI")
    ap.add_argument("--plan",     type=Path, default=PLAN_DEFAULT)
    ap.add_argument("--imgdir",   type=Path, default=IMGDIR_DEFAULT)
    ap.add_argument("--outdir",   type=Path, default=OUTDIR_DEFAULT)
    ap.add_argument("--duration", type=int, default=DEF_SEC, help="seconds, 1–10 depending on model")
    ap.add_argument("--resolution", type=str, default=DEF_RES, choices=["480p", "720p", "1080p", "4K"])
    ap.add_argument("--model", type=str, default=DEF_MODEL,
                    choices=["sora-2","higgsfield_v1","kling_25","veo_3","higgsfield_soul","nanobanana-video","pixverse","ltxv-13b","seedance","wan-25"])
    ap.add_argument("--clips",    type=int, default=0, help="limit to first N clips")
    ap.add_argument("--public-image", type=str, default="", help="override image URL for single-clip debug")
    ap.add_argument("--prompt", type=str, default="", help="override prompt for single-clip debug")
    ap.add_argument("--generate-endpoint", dest="generate_endpoint", type=str, default=GENERATE_ENDPOINT_DEFAULT)
    ap.add_argument("--status-base", type=str, default=STATUS_BASE_DEFAULT)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    if args.debug:
        print("[DEBUG] HIGGS_BASE_URL:", os.getenv("HIGGS_BASE_URL"))
        print("[DEBUG] API_BASE:", API_BASE)
        print("[DEBUG] HIGGS_VID_API_KEY present:", bool(os.getenv("HIGGS_VID_API_KEY")))
        print("[DEBUG] VIDEOGEN_API_KEY present:", bool(os.getenv("VIDEOGEN_API_KEY")))
        print("[DEBUG] Using key length:", len(API_KEY) if API_KEY else 0)

    if not API_KEY:
        die("Missing HIGGS_VID_API_KEY (or VIDEOGEN_API_KEY / HIGGS_VEO_API_KEY) in environment")

    if not args.plan.exists():
        die(f"Missing plan: {args.plan}")
    args.outdir.mkdir(parents=True, exist_ok=True)

    plan = read_plan(args.plan)
    clips = plan.get("clips", [])
    if not clips:
        die("No clips in plan.")
    if args.clips and args.clips > 0:
        clips = clips[: args.clips]

    remote_map = load_remote_map(MAP_PATH)

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    total = len(clips)
    for i, clip in enumerate(clips, start=1):
        idx = int(clip.get("index", i) or i)

        if args.public_image:
            image_src, is_remote = _clean_url(args.public_image), True
            if not _URL_RE.match(image_src):
                die(f"--public-image is not a valid http(s) URL: {image_src}")
        else:
            image_src, is_remote = local_or_remote_png(idx, args.imgdir, remote_map)
        if not is_remote:
            die(f"clip{idx}: provider needs a public URL. Create {MAP_PATH.name} first.")

        prompt = build_prompt(clip, args.prompt or None)

        payload = {
            "model": args.model,
            "prompt": prompt,
            "image_url": image_src,                         # image-to-video mode
            "duration": max(1, min(MAX_SEC, int(args.duration))),
            "resolution": args.resolution,
        }

        print(f"[{i}/{total}] submit…")
        r = post_json(args.generate_endpoint, headers, payload, args.debug)
        if not (200 <= r.status_code < 300):
            die(f"generate error: {r.status_code} {r.text[:1500]}")

        try:
            data = r.json()
        except Exception:
            die(f"Generate response not JSON: {r.text[:500]}")

        if not data.get("success", True):
            die(f"API reported failure: {data}")

        gen_id = data.get("generation_id")
        if not gen_id:
            status_url = (data.get("status_url") or "").strip()
            if status_url:
                gen_id = status_url.strip("/").split("/")[-1]
        if not gen_id:
            die(f"Response missing generation_id: {data}")

        result = poll_generation(args.status_base, gen_id, headers, args.debug, min_interval=4.0, max_wait=900)

        video_url: Optional[str] = result.get("video_url")
        if not video_url:
            die(f"No video_url in status: {result}")

        out_path = args.outdir / f"clip{idx}.mp4"
        # Public access per provider docs; no auth required for download
        with requests.get(video_url, stream=True, timeout=180) as dl:
            if dl.status_code != 200:
                die(f"download failed {dl.status_code} {video_url}\n{dl.text[:500]}")
            with open(out_path, "wb") as f:
                for chunk in dl.iter_content(1 << 20):
                    if chunk:
                        f.write(chunk)

        if args.debug:
            print(f"[DEBUG] saved {out_path} ({out_path.stat().st_size} bytes)")
        print(f"[{i}/{total}] wrote {out_path.name}")

    print(f"Done. Wrote {total} file(s) → {args.outdir.resolve()}")

if __name__ == "__main__":
    main()
