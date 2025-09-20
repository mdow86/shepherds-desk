#!/usr/bin/env python3
# gen_tts_piper.py — emits MP3 by default, quiet subprocess output
"""
Generate TTS audio from plan.json using Piper.
Default output: MP3 (44.1 kHz, ~128 kbps) via ffmpeg.

Requires:
  - Piper binary + voice model (kept under tools-local/piper/)
  - ffmpeg in PATH (for MP3)

CLI:
  python gen_tts_piper.py
  python gen_tts_piper.py --format wav_24000
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------- project paths ----------
try:
    import paths  # if a shim exists alongside the script
except Exception:
    from helpers import paths  # real location under src/media_generator/helpers

PLAN_DEFAULT   = paths.OUTPUTS / "plan.json"
OUTDIR_DEFAULT = paths.OUTPUTS / "audio"

# Resolve repo root robustly: prefer paths.PROJECT_ROOT, then legacy BASE, then .../src -> repo root
_BIN_NAME = "piper.exe" if os.name == "nt" else "piper"
PROJECT_ROOT: Path = (
    getattr(paths, "PROJECT_ROOT", None)
    or getattr(paths, "BASE", None)  # backward compatibility
    or Path(paths.CODE_ROOT).resolve().parents[1]  # .../src -> repo root
)

TOOLS_LOCAL = PROJECT_ROOT / "tools-local" / "piper"
DEFAULT_EXE = TOOLS_LOCAL / _BIN_NAME

# ---------- discovery ----------
def discover_piper_exe() -> Path:
    # 1) explicit env
    env = os.getenv("PIPER_EXE")
    if env:
        p = Path(env)
        if p.exists():
            return p
    # 2) repo tools-local from PROJECT_ROOT
    if DEFAULT_EXE.exists():
        return DEFAULT_EXE
    # 3) walk upward from this file to find tools-local/piper
    here = Path(__file__).resolve()
    for parent in (here,) + tuple(here.parents):
        cand = parent / "tools-local" / "piper" / _BIN_NAME
        if cand.exists():
            return cand
    # 4) PATH
    which = shutil.which("piper")
    if which:
        return Path(which)
    print("Piper executable not found. Set PIPER_EXE or place it at tools-local/piper/", file=sys.stderr)
    sys.exit(1)

def _first_onnx_under(dirpath: Path) -> Optional[Path]:
    for pat in ("**/*.onnx", "*.onnx"):
        for p in dirpath.glob(pat):
            if p.is_file():
                return p
    return None

def discover_piper_model() -> Path:
    # 1) explicit env
    env = os.getenv("PIPER_MODEL")
    if env:
        p = Path(env)
        if p.exists():
            return p
    # 2) repo tools-local voices/models
    for sub in ("voices", "models", "."):
        cand = _first_onnx_under(TOOLS_LOCAL / sub)
        if cand:
            return cand
    print(
        "Piper model (.onnx) not found. Set PIPER_MODEL or place a voice under tools-local/piper/voices/",
        file=sys.stderr,
    )
    sys.exit(1)

# ---------- plan I/O ----------
def load_plan(plan_path: Path) -> Dict[str, Any]:
    try:
        return json.loads(plan_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"Plan not found: {plan_path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON in plan: {e}", file=sys.stderr)
        sys.exit(1)

# ---------- text shaping ----------
def sanitize(text: str) -> str:
    return " ".join((text or "").strip().split())

def clip_to_speech(clip: Dict[str, Any]) -> str:
    # v1: 'dialogue'; v2: verse.text (+ optional verse.ref) + dialogue_text
    if "dialogue_text" in clip or "verse" in clip:
        parts: List[str] = []
        verse = clip.get("verse") or {}
        if verse.get("text"):
            ref = (verse.get("ref") or "").strip()
            parts.append(f"{verse['text']} ({ref})." if ref else verse["text"])
        if clip.get("dialogue_text"):
            parts.append(clip["dialogue_text"])
        return sanitize(" ".join(parts))
    return sanitize(clip.get("dialogue", ""))

# ---------- synthesis helpers ----------
def run_piper_wav(text: str, exe: Path, model: Path, wav_out: Path) -> None:
    wav_out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(exe), "-m", str(model), "-f", str(wav_out)]
    subprocess.run(
        cmd,
        input=text.encode("utf-8"),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

def ffmpeg_to_mp3(wav_path: Path, mp3_path: Path) -> None:
    if not shutil.which("ffmpeg"):
        print("ffmpeg not found in PATH. Install ffmpeg or use --format wav_24000.", file=sys.stderr)
        sys.exit(1)
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(wav_path),
        "-ac", "1", "-ar", "44100", "-b:a", "128k",
        str(mp3_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ---------- CLI ----------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Generate audio from plan.json using Piper")
    ap.add_argument("--plan", type=Path, default=PLAN_DEFAULT)
    ap.add_argument("--outdir", type=Path, default=OUTDIR_DEFAULT)
    ap.add_argument("--exe", type=Path, default=discover_piper_exe())
    ap.add_argument("--model", type=Path, default=discover_piper_model())
    ap.add_argument("--format", choices=["mp3_44100_128", "wav_24000"], default="mp3_44100_128")
    return ap

# ---------- main ----------
def main() -> None:
    args = build_parser().parse_args()

    if not args.exe.exists():
        print("Piper executable not found:", args.exe, file=sys.stderr)
        sys.exit(1)
    if not args.model.exists():
        print("Piper model not found:", args.model, file=sys.stderr)
        sys.exit(1)

    plan = load_plan(args.plan)
    clips = plan.get("clips", [])
    if not clips:
        print("Plan has no clips.", file=sys.stderr)
        sys.exit(1)

    wrote = 0
    total = len(clips)

    for i, clip in enumerate(clips, start=1):
        idx = int(clip.get("index", i) or i)
        speech = clip_to_speech(clip)
        if not speech:
            print(f"[{idx}/{total}] empty; skip")
            continue

        if args.format == "mp3_44100_128":
            out_path = args.outdir / f"clip{idx}.mp3"
            print(f"[{idx}/{total}] synth → mp3 … ", end="", flush=True)
            with tempfile.TemporaryDirectory() as td:
                tmp_wav = Path(td) / "tmp.wav"
                try:
                    run_piper_wav(speech, args.exe, args.model, tmp_wav)
                    ffmpeg_to_mp3(tmp_wav, out_path)
                except Exception as e:
                    rc = getattr(e, "returncode", 1)
                    print("fail")
                    print(f"Clip {idx}: Piper/ffmpeg failed (exit {rc})", file=sys.stderr)
                    sys.exit(rc)
            print("done")
        else:
            out_path = args.outdir / f"clip{idx}.wav"
            print(f"[{idx}/{total}] synth → wav … ", end="", flush=True)
            try:
                run_piper_wav(speech, args.exe, args.model, out_path)
            except Exception as e:
                rc = getattr(e, "returncode", 1)
                print("fail")
                print(f"Clip {idx}: Piper failed (exit {rc})", file=sys.stderr)
                sys.exit(rc)
            print("done")

        wrote += 1

    if wrote:
        print(f"Done. Wrote {wrote} file(s) → {args.outdir.resolve()}")
    else:
        print("No files generated.", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
