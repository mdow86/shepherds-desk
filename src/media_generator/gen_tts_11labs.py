#!/usr/bin/env python3
"""
Generate per-clip audio from plan.json using ElevenLabs with language presets.

Languages:
  --language english|spanish|japanese
Voices can be overridden via:
  ELEVEN_VOICE_EN, ELEVEN_VOICE_ES, ELEVEN_VOICE_JA   (or --voice-id)
Defaults fall back to known public IDs if present; override in .env for production.

Prereq: pip install elevenlabs python-dotenv
Auth:   ELEVENLABS_API_KEY in env or .env
"""
from __future__ import annotations

import argparse, json, os, sys
from pathlib import Path
from typing import Any, Dict, Iterable, Union

try:
    import paths
except Exception:
    from helpers import paths

PLAN_DEFAULT   = paths.OUTPUTS / "plan.json"
OUTDIR_DEFAULT = paths.OUTPUTS / "audio"

MODEL_DEFAULT = os.getenv("ELEVEN_MODEL", "eleven_v3")
#MODEL_DEFAULT  = os.getenv("ELEVEN_MODEL", "el#even_multilingual_v2")
FORMAT_DEFAULT = os.getenv("ELEVEN_FORMAT", "mp3_44100_128")

VOICE_EN_DEFAULT = os.getenv("ELEVEN_VOICE_EN", "cVd39cx0VtXNC13y5Y7z")
VOICE_ES_DEFAULT = os.getenv("ELEVEN_VOICE_ES", "zl1Ut8dvwcVSuQSB9XkG")
VOICE_JA_DEFAULT = os.getenv("ELEVEN_VOICE_JA", "cgSgspJ2msm6clMCkdW9")

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    from elevenlabs.client import ElevenLabs
except ImportError:
    print("Missing dependency: pip install elevenlabs python-dotenv", file=sys.stderr)
    sys.exit(1)

def load_plan(plan_path: Path) -> Dict[str, Any]:
    try:
        return json.loads(plan_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"Plan not found: {plan_path}", file=sys.stderr); sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON in plan: {e}", file=sys.stderr); sys.exit(1)

def sanitize(text: str) -> str:
    return " ".join((text or "").strip().split())

def clip_text(clip: Dict[str, Any]) -> str:
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

def ext_for(fmt: str) -> str:
    f = (fmt or "").lower()
    if f.startswith("mp3"):  return ".mp3"
    if f.startswith("opus"): return ".opus"
    if f.startswith("pcm"):  return ".wav"
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
        wf.setsampwidth(sampwidth_bytes)
        wf.setframerate(sr)
        wf.writeframes(pcm)
    return bio.getvalue()

def pcm_samplerate(fmt: str) -> int:
    try:
        return int((fmt or "").split("_", 1)[1])
    except Exception:
        return 44100

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="TTS via ElevenLabs → per-clip audio files")
    ap.add_argument("--plan", type=Path, default=PLAN_DEFAULT)
    ap.add_argument("--outdir", type=Path, default=OUTDIR_DEFAULT)
    ap.add_argument("--language", type=str, choices=["english", "spanish", "japanese"], default="english")
    ap.add_argument("--voice-id", type=str, default="", help="Override voice ID; otherwise chosen from language preset/env")
    ap.add_argument("--model-id", type=str, default=MODEL_DEFAULT)
    ap.add_argument("--format", type=str, default=FORMAT_DEFAULT,
                    help="mp3_* | opus_* | pcm_* | ulaw_8000 | alaw_8000")
    return ap

def main() -> None:
    args = build_parser().parse_args()

    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        print("Missing ELEVENLABS_API_KEY (set in env or .env)", file=sys.stderr); sys.exit(1)

    if not args.plan.exists():
        print(f"Missing plan: {args.plan}", file=sys.stderr); sys.exit(1)
    args.outdir.mkdir(parents=True, exist_ok=True)

    client = ElevenLabs(api_key=api_key)
    plan = load_plan(args.plan)
    clips = plan.get("clips", [])
    if not clips:
        print("Plan has no clips.", file=sys.stderr); sys.exit(1)

    if args.voice_id:
        voice_id = args.voice_id
    else:
        if args.language == "spanish":
            voice_id = VOICE_ES_DEFAULT
        elif args.language == "japanese":
            voice_id = VOICE_JA_DEFAULT
        else:
            voice_id = VOICE_EN_DEFAULT

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
        print(f"[{idx}/{total}] TTS ({args.language}) → {out_path.name}")
        try:
            stream = client.text_to_speech.convert(
                text=text,
                voice_id=voice_id,
                model_id=args.model_id,
                output_format=args.format,
            )
            data = read_all(stream)
            if args.format.lower().startswith("pcm"):
                data = wrap_pcm_to_wav(data, pcm_samplerate(args.format))
            out_path.write_bytes(data)
            wrote += 1
        except Exception as e:
            print(f"Clip {idx}: ElevenLabs conversion failed: {e}", file=sys.stderr); sys.exit(1)

    if wrote:
        print(f"Done. Wrote {wrote} file(s) → {args.outdir.resolve()}")
    else:
        print("No files generated.", file=sys.stderr); sys.exit(1)

if __name__ == "__main__":
    main()
