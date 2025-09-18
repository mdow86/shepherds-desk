# video_compose.py
"""
Stitches plan.json + generated images + audio into a single MP4.

- Accepts .mp3, .wav, .opus, .ulaw, .alaw per clip.
- If audio is longer than the slot, extend image duration to match.
- Writes subtitles.srt.

Run:
  python video_compose.py
  python video_compose.py --plan outputs/plan.json --imgdir outputs/images --audiodir outputs/audio --out outputs/video/final.mp4
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import numpy as np

from moviepy.editor import (
    ImageClip, AudioFileClip, concatenate_videoclips,
    AudioClip, concatenate_audioclips
)
from moviepy.audio.AudioClip import AudioArrayClip

DEFAULT_PLAN = Path("outputs/plan.json")
DEFAULT_IMGDIR = Path("outputs/images")
DEFAULT_AUDIODIR = Path("outputs/audio")
DEFAULT_OUT = Path("outputs/video/final.mp4")

DEFAULT_SR = 24000  # used for synthetic silence
FPS = 30

AUDIO_EXTS = (".mp3", ".wav", ".opus", ".ulaw", ".alaw")

# ---------- Plan helpers ----------
def load_plan(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Cannot read plan: {e}", file=sys.stderr)
        sys.exit(1)

def get_clip_times(clip: dict, default_index: int) -> tuple[float, float]:
    if "start_sec" in clip and "end_sec" in clip:
        return float(clip["start_sec"]), float(clip["end_sec"])
    start = (default_index - 1) * 10.0
    return start, start + 10.0

def clip_spoken_text(clip: dict) -> str:
    if "dialogue_text" in clip or "verse" in clip:
        parts = []
        v = clip.get("verse") or None
        if v and v.get("text"):
            ref = v.get("ref", "").strip()
            parts.append(f"{v['text']} ({ref})." if ref else v["text"])
        if clip.get("dialogue_text"):
            parts.append(clip["dialogue_text"])
        return " ".join(" ".join(parts).split())
    return " ".join((clip.get("dialogue", "") or "").split())

def clip_subtitle(clip: dict) -> str:
    s = clip.get("subtitle")
    return " ".join(s.strip().split()) if isinstance(s, str) and s.strip() else clip_spoken_text(clip)

# ---------- SRT helpers ----------
def srt_escape(text: str) -> str:
    return (text or "").replace("\n", " ").strip()

def to_srt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    hh = ms // (3600 * 1000); ms %= 3600 * 1000
    mm = ms // (60 * 1000);   ms %= 60 * 1000
    ss = ms // 1000;          ms %= 1000
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"

def write_srt(plan: dict, srt_path: Path) -> None:
    lines = []
    for clip in plan.get("clips", []):
        idx = int(clip["index"])
        start, end = get_clip_times(clip, idx)
        lines.append(f"{idx}")
        lines.append(f"{to_srt_timestamp(start)} --> {to_srt_timestamp(end)}")
        lines.append(srt_escape(clip_subtitle(clip)))
        lines.append("")
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text("\n".join(lines), encoding="utf-8")

# ---------- Audio helpers ----------
def make_silence_array(duration: float, fps: int, nchannels: int) -> AudioArrayClip:
    n = max(1, int(round(duration * fps)))
    arr = np.zeros((n, nchannels), dtype=np.float32)
    return AudioArrayClip(arr, fps=fps)

def find_audio_for_clip(aud_dir: Path, idx: int) -> Path | None:
    for ext in AUDIO_EXTS:
        p = aud_dir / f"clip{idx}{ext}"
        if p.exists():
            return p
    return None

def fit_audio_to_slot(aud_path: Path | None, slot_dur: float) -> AudioClip:
    if aud_path and aud_path.exists():
        try:
            a = AudioFileClip(str(aud_path))
        except Exception as e:
            print(f"Warning: failed reading audio {aud_path}: {e}. Using silence.", file=sys.stderr)
            return make_silence_array(slot_dur, fps=DEFAULT_SR, nchannels=1)

        fps = int(a.fps)
        nch = int(getattr(a, "nchannels", 1))
        if a.duration > slot_dur:
            return a.subclip(0, slot_dur)
        if a.duration < slot_dur:
            pad = make_silence_array(slot_dur - a.duration, fps=fps, nchannels=nch)
            return concatenate_audioclips([a, pad])
        return a
    return make_silence_array(slot_dur, fps=DEFAULT_SR, nchannels=1)

# ---------- Build video ----------
def build_video(plan_path: Path, img_dir: Path, aud_dir: Path, out_path: Path) -> None:
    plan = load_plan(plan_path)
    clips = plan.get("clips", [])
    if not clips:
        print("No clips in plan.", file=sys.stderr)
        sys.exit(1)

    segs = []
    for c in clips:
        idx = int(c["index"])
        img_path = img_dir / f"clip{idx}.png"
        if not img_path.exists():
            print(f"Missing image: {img_path}", file=sys.stderr)
            sys.exit(1)

        start, end = get_clip_times(c, idx)
        slot_dur = max(0.01, float(end - start))

        aud_path = find_audio_for_clip(aud_dir, idx)
        a = fit_audio_to_slot(aud_path, slot_dur)
        dur = max(slot_dur, getattr(a, "duration", slot_dur))

        v = ImageClip(str(img_path), duration=dur).set_fps(FPS).set_audio(a)
        segs.append(v)

    final = concatenate_videoclips(segs, method="compose")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    final.write_videofile(
        str(out_path),
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        threads=0,
        preset="medium",
        bitrate="4000k",
    )

    srt_path = out_path.with_suffix(".srt")
    write_srt(plan, srt_path)
    print(f"Wrote: {out_path}")
    print(f"Wrote: {srt_path}")

def main():
    ap = argparse.ArgumentParser(description="Compose images+audio into a single MP4 from plan.json")
    ap.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    ap.add_argument("--imgdir", type=Path, default=DEFAULT_IMGDIR)
    ap.add_argument("--audiodir", type=Path, default=DEFAULT_AUDIODIR)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    build_video(args.plan, args.imgdir, args.audiodir, args.out)

if __name__ == "__main__":
    main()
