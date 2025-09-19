# gen_tts_11labs.py
"""
Generate per-clip audio from plan.json using ElevenLabs.

Design
- Cross-platform paths via paths.py
- Minimal flags; sane defaults
- Plan v1 (dialogue) and v2 (verse{ref,text} + dialogue_text)
- Output format default is mp3_44100_128 (moviepy-friendly)
- Optional PCM formats are wrapped into WAV

Prereq
  pip install elevenlabs python-dotenv

Auth
  .env or environment must contain ELEVENLABS_API_KEY=...

Usage
  python gen_tts_11labs.py
  python gen_tts_11labs.py --voice-id <VOICE> --model-id eleven_multilingual_v2 --format mp3_44100_128
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Union

# --------------------------- Project paths ----------------------------------

try:
    import paths
except Exception as e:
    print("Failed to import paths.py — ensure it's on PYTHONPATH:", e, file=sys.stderr)
    sys.exit(2)

PLAN_DEFAULT = Path(paths.OUTPUTS) / "plan.json"
OUTDIR_DEFAULT = Path(paths.OUTPUTS) / "audio"
MODEL_DEFAULT = os.getenv("ELEVEN_MODEL", "eleven_multilingual_v2")
VOICE_DEFAULT = os.getenv("ELEVEN_VOICE", "cVd39cx0VtXNC13y5Y7z")  # replace if you have a preferred voice
FORMAT_DEFAULT = os.getenv("ELEVEN_FORMAT", "mp3_44100_128")

# --------------------------- Optional .env load ------------------------------

try:
    from dotenv import load_dotenv  # convenience for local dev; optional
    load_dotenv()
except Exception:
    pass

# ------------------------------ Client import --------------------------------

try:
    from elevenlabs.client import ElevenLabs
except ImportError:
    print("Missing dependency: pip install elevenlabs python-dotenv", file=sys.stderr)
    sys.exit(1)

# ------------------------------ Plan I/O ------------------------------------

def load_plan(plan_path: Path) -> Dict[str, Any]:
    try:
        return json.loads(plan_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"Plan not found: {plan_path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON in plan: {e}", file=sys.stderr)
        sys.exit(1)

# ------------------------------ Text shaping --------------------------------

def sanitize(text: str) -> str:
    return " ".join((text or "").strip().split())

def clip_text(clip: Dict[str, Any]) -> str:
    """
    v1: use 'dialogue'
    v2: verse.text (+ optional verse.ref) + dialogue_text
    """
    if "dialogue_text" in clip or "verse" in clip:
        parts = []
        verse = clip.get("verse") or {}
        if verse.get("text"):
            ref = (verse.get("ref") or "").strip()
            parts.append(f"{verse['text']} ({ref})." if ref else verse["text"])
        if clip.get("dialogue_text"):
            parts.append(clip["dialogue_text"])
        return sanitize(" ".join(parts))
    return sanitize(clip.get("dialogue", ""))

# ------------------------------ Format helpers ------------------------------

def ext_for(fmt: str) -> str:
    f = (fmt or "").lower()
    if f.startswith("mp3"):  return ".mp3"
    if f.startswith("opus"): return ".opus"
    if f.startswith("pcm"):  return ".wav"   # we wrap raw PCM into WAV
    if f.startswith("ulaw"): return ".ulaw"
    if f.startswith("alaw"): return ".alaw"
    return ".bin"

def read_all(stream: Union[bytes, bytearray, Iterable[bytes]]) -> bytes:
    if isinstance(stream, (bytes, bytearray)):
        return bytes(stream)
    buf = bytearray()
    for chunk in stream:
        buf.extend(chunk)
    return bytes(buf)

def wrap_pcm_to_wav(pcm: bytes, sr: int, ch: int = 1, sampwidth_bytes: int = 2) -> bytes:
    import io, wave
    bio = io.BytesIO()
    with wave.open(bio, "wb") as wf:
        wf.setnchannels(ch)
        wf.setsampwidth(sampwidth_bytes)   # 16-bit
        wf.setframerate(sr)
        wf.writeframes(pcm)
    return bio.getvalue()

def pcm_samplerate(fmt: str) -> int:
    try:
        # e.g., "pcm_44100" → 44100
        return int((fmt or "").split("_", 1)[1])
    except Exception:
        return 44100

# --------------------------------- CLI --------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="TTS via ElevenLabs → per-clip audio files")
    ap.add_argument("--plan", type=Path, default=PLAN_DEFAULT)
    ap.add_argument("--outdir", type=Path, default=OUTDIR_DEFAULT)
    ap.add_argument("--voice-id", type=str, default=VOICE_DEFAULT)
    ap.add_argument("--model-id", type=str, default=MODEL_DEFAULT)
    ap.add_argument("--format", type=str, default=FORMAT_DEFAULT,
                    help="mp3_* | opus_* | pcm_* | ulaw_8000 | alaw_8000")
    return ap

# --------------------------------- Main -------------------------------------

def main() -> None:
    args = build_parser().parse_args()

    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        print("Missing ELEVENLABS_API_KEY (set in env or .env)", file=sys.stderr)
        sys.exit(1)

    if not args.plan.exists():
        print(f"Missing plan: {args.plan}", file=sys.stderr)
        sys.exit(1)
    args.outdir.mkdir(parents=True, exist_ok=True)

    client = ElevenLabs(api_key=api_key)
    plan = load_plan(args.plan)
    clips = plan.get("clips", [])
    if not clips:
        print("Plan has no clips.", file=sys.stderr)
        sys.exit(1)

    out_ext = ext_for(args.format)
    wrote = 0
    total = len(clips)

    for i, clip in enumerate(clips, start=1):
        idx = int(clip.get("index", i) or i)
        text = clip_text(clip)
        if not text:
            print(f"Clip {idx}: empty; skipping.", file=sys.stderr)
            continue

        out_path = args.outdir / f"clip{idx}{out_ext}"
        print(f"[{idx}/{total}] TTS → {out_path.name}")
        try:
            stream = client.text_to_speech.convert(
                text=text,
                voice_id=args.voice_id,
                model_id=args.model_id,
                output_format=args.format,
            )
            data = read_all(stream)
            if args.format.lower().startswith("pcm"):
                data = wrap_pcm_to_wav(data, pcm_samplerate(args.format))
            out_path.write_bytes(data)
            wrote += 1
        except Exception as e:
            print(f"Clip {idx}: ElevenLabs conversion failed: {e}", file=sys.stderr)
            sys.exit(1)

    if wrote:
        print(f"Done. Wrote {wrote} file(s) → {args.outdir.resolve()}")
    else:
        print("No files generated.", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
