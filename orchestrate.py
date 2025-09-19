# orchestrate.py — minimal, with auto-named video outputs
"""
Stages:
- GLOO → writes plan.json
- TTS  → Piper or ElevenLabs
- IMG  → SD WebUI or Gemini
- VIDEO → composes MP4, auto-names from plan title

Flags:
  --only {gloo,tts,img,video}
  --skip-gloo --skip-tts --skip-img --skip-video
  --tts {piper,elevenlabs}   (default piper)
  --img {sd,gemini}          (default sd)
"""

from __future__ import annotations
import argparse
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

import paths  # shared paths.py

ROOT = Path(__file__).resolve().parent
PLAN = paths.OUTPUTS / "plan.json"
CODE = Path(getattr(paths, "CODE_ROOT", ROOT))

def exists_plan() -> bool:
    if not PLAN.exists():
        return False
    try:
        data = json.loads(PLAN.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(data.get("title") or any(isinstance(v, list) and v for v in data.values()))

def load_title() -> str:
    try:
        return json.loads(PLAN.read_text(encoding="utf-8")).get("title", "")
    except Exception:
        return ""

def run_step(name: str, cmd: list[str]) -> None:
    t0 = time.perf_counter()
    logging.info("[%s] → %s", name, " ".join(cmd))
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        dt = time.perf_counter() - t0
        logging.error("[%s] FAILED in %.1fs (exit %s)", name, dt, e.returncode)
        sys.exit(e.returncode)
    dt = time.perf_counter() - t0
    logging.info("[%s] OK in %.1fs", name, dt)

# ---------- command builders ----------
def tts_cmd_piper(a: argparse.Namespace) -> list[str]:
    return [
        sys.executable, str(CODE / "gen_tts_piper.py"),
        "--plan", str(PLAN),
        "--outdir", str(paths.OUTPUTS / "audio"),
    ]

def tts_cmd_eleven(a: argparse.Namespace) -> list[str]:
    return [
        sys.executable, str(CODE / "gen_tts_11labs.py"),
        "--plan", str(PLAN),
        "--outdir", str(paths.OUTPUTS / "audio"),
    ]

def img_cmd_sd(a: argparse.Namespace) -> list[str]:
    return [
        sys.executable, str(CODE / "gen_img_sd.py"),
        "--plan", str(PLAN),
        "--outdir", str(paths.OUTPUTS / "images"),
    ]

def img_cmd_gemini(a: argparse.Namespace) -> list[str]:
    return [
        sys.executable, str(CODE / "gen_img_gemini.py"),
        "--plan", str(PLAN),
        "--outdir", str(paths.OUTPUTS / "images"),
    ]

def video_cmd(a: argparse.Namespace) -> list[str]:
    # Do NOT pass --out; let video_compose auto-name from title
    return [
        sys.executable, str(CODE / "video_compose.py"),
        "--plan", str(PLAN),
        "--imgdir", str(paths.OUTPUTS / "images"),
        "--audiodir", str(paths.OUTPUTS / "audio"),
        "--outdir", str(paths.OUTPUTS / "video"),
    ]

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Simple orchestrator")
    p.add_argument("--only", choices=["gloo", "tts", "img", "video"])
    p.add_argument("--skip-gloo", action="store_true")
    p.add_argument("--skip-tts", action="store_true")
    p.add_argument("--skip-img", action="store_true")
    p.add_argument("--skip-video", action="store_true")
    p.add_argument("--tts", choices=["piper", "elevenlabs"], default="piper")
    p.add_argument("--img", choices=["sd", "gemini"], default="sd")
    p.add_argument("--log", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p

def main() -> None:
    a = build_parser().parse_args()
    logging.basicConfig(level=getattr(logging, a.log), format="%(message)s")

    def enabled(stage: str) -> bool:
        if a.only and stage != a.only:
            return False
        if stage == "gloo" and a.skip_gloo:   return False
        if stage == "tts"  and a.skip_tts:    return False
        if stage == "img"  and a.skip_img:    return False
        if stage == "video"and a.skip_video:  return False
        return True

    logging.info("CODE=%s", paths.CODE_ROOT)

    # 1) GLOO
    if enabled("gloo"):
        run_step("GLOO", [sys.executable, str(CODE / "api_call.py")])
    else:
        logging.info("[GLOO] skipped")

    if not exists_plan():
        logging.error("plan.json missing or empty")
        sys.exit(2)
    logging.info("[PLAN] %s — Title: %s", PLAN, load_title())

    # 2) TTS
    if enabled("tts"):
        run_step("TTS", tts_cmd_piper(a) if a.tts == "piper" else tts_cmd_eleven(a))
    else:
        logging.info("[TTS] skipped")

    # 3) IMG
    if enabled("img"):
        run_step("IMG", img_cmd_sd(a) if a.img == "sd" else img_cmd_gemini(a))
    else:
        logging.info("[IMG] skipped")

    # 4) VIDEO
    if enabled("video"):
        run_step("VIDEO", video_cmd(a))
    else:
        logging.info("[VIDEO] skipped")

    logging.info("DONE")

if __name__ == "__main__":
    main()
