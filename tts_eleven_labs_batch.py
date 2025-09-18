# tts_eleven_labs_batch.py
"""
Batch-generate audio from outputs/plan.json using ElevenLabs.

- Plan v1/v2 supported (dialogue_text + verse{ref,text}).
- Default format = mp3_44100_128 (MoviePy-friendly).
- Valid formats: mp3_* | opus_* | pcm_* | ulaw_8000 | alaw_8000.
  If pcm_* is used, raw PCM is wrapped into a WAV container and saved as .wav.

Env:
  ELEVENLABS_API_KEY from .env or environment.

CLI:
  python tts_eleven_labs_batch.py
  --plan outputs/plan.json --outdir outputs/audio
  --voice-id JBFqnCBsd6RMkjVDRZzb --model-id eleven_multilingual_v2
  --format mp3_44100_128 | opus_48000_128 | pcm_44100 | ulaw_8000 | alaw_8000
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
from typing import Iterable, Union

try:
    from dotenv import load_dotenv; load_dotenv()
except Exception:
    pass

try:
    from elevenlabs.client import ElevenLabs
except ImportError:
    print("Missing dependency: pip install elevenlabs python-dotenv", file=sys.stderr)
    sys.exit(1)

DEFAULT_PLAN = Path("outputs/plan.json")
DEFAULT_OUTDIR = Path("outputs/audio")
DEFAULT_MODEL_ID = "eleven_multilingual_v2"
DEFAULT_VOICE_ID = "cVd39cx0VtXNC13y5Y7z"
DEFAULT_FORMAT = "mp3_44100_128"

def load_plan(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"Plan not found: {p}", file=sys.stderr); sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON in plan: {e}", file=sys.stderr); sys.exit(1)

def sanitize(s: str) -> str:
    return " ".join((s or "").strip().split())

def clip_text(c: dict) -> str:
    if "dialogue_text" in c or "verse" in c:
        parts = []
        v = c.get("verse") or {}
        if v.get("text"):
            ref = (v.get("ref") or "").strip()
            parts.append(f"{v['text']} ({ref})." if ref else v["text"])
        if c.get("dialogue_text"):
            parts.append(c["dialogue_text"])
        return sanitize(" ".join(parts))
    return sanitize(c.get("dialogue", ""))

def read_all(stream: Union[bytes, Iterable[bytes]]) -> bytes:
    if isinstance(stream, (bytes, bytearray)): return bytes(stream)
    buf = bytearray()
    for chunk in stream: buf.extend(chunk)
    return bytes(buf)

def ext_for(fmt: str) -> str:
    f = fmt.lower()
    if f.startswith("mp3"):  return ".mp3"
    if f.startswith("opus"): return ".opus"
    if f.startswith("pcm"):  return ".wav"  # wrapped below
    if f.startswith("ulaw"): return ".ulaw"
    if f.startswith("alaw"): return ".alaw"
    return ".bin"

def wrap_pcm_to_wav(pcm: bytes, sr: int, ch: int = 1, sampwidth_bytes: int = 2) -> bytes:
    import io, wave
    bio = io.BytesIO()
    with wave.open(bio, "wb") as wf:
        wf.setnchannels(ch)
        wf.setsampwidth(sampwidth_bytes)  # 16-bit
        wf.setframerate(sr)
        wf.writeframes(pcm)
    return bio.getvalue()

def pcm_samplerate(fmt: str) -> int:
    try: return int(fmt.split("_", 1)[1])
    except Exception: return 44100

def main() -> None:
    ap = argparse.ArgumentParser(description="TTS via ElevenLabs → per-clip audio files")
    ap.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    ap.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    ap.add_argument("--voice-id", type=str, default=DEFAULT_VOICE_ID)
    ap.add_argument("--model-id", type=str, default=DEFAULT_MODEL_ID)
    ap.add_argument("--format", type=str, default=DEFAULT_FORMAT)
    args = ap.parse_args()

    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        print("Missing ELEVENLABS_API_KEY", file=sys.stderr); sys.exit(1)

    client = ElevenLabs(api_key=api_key)
    plan = load_plan(args.plan)
    clips = plan.get("clips", [])
    if not clips:
        print("Plan has no clips.", file=sys.stderr); sys.exit(1)

    out_ext = ext_for(args.format)
    wrote = 0
    for c in clips:
        idx = int(c.get("index", 0) or 0)
        text = clip_text(c)
        if not text:
            print(f"Clip {idx}: empty; skipping.", file=sys.stderr)
            continue

        out_path = args.outdir / f"clip{idx}{out_ext}"
        print(f"[{idx}/{len(clips)}] TTS → {out_path.name}")
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

            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(data)
            wrote += 1
        except Exception as e:
            print(f"Clip {idx}: ElevenLabs conversion failed: {e}", file=sys.stderr)
            sys.exit(1)

    if wrote:
        print(f"Done. Wrote {wrote} files → {args.outdir.resolve()}")
    else:
        print("No files generated.", file=sys.stderr); sys.exit(1)

if __name__ == "__main__":
    main()
