# video_compose.py
"""
Compose images + audio into MP4 using durations derived from audio.
Auto-names outputs from plan title: <slug>_001.mp4, <slug>_002.mp4, ...
Writes matching .srt.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import List, Dict, Any

import numpy as np
from moviepy.editor import (
    ImageClip, AudioFileClip, concatenate_videoclips,
    AudioClip, concatenate_audioclips
)
from moviepy.audio.AudioClip import AudioArrayClip

try:
    import paths
except Exception as e:
    print("Failed to import paths.py — ensure it's on PYTHONPATH:", e, file=sys.stderr)
    sys.exit(2)

PLAN_DEFAULT     = Path(paths.OUTPUTS) / "plan.json"
IMGDIR_DEFAULT   = Path(paths.OUTPUTS) / "images"
AUDIODIR_DEFAULT = Path(paths.OUTPUTS) / "audio"
VIDDIR_DEFAULT   = Path(paths.OUTPUTS) / "video"   # final path decided at runtime

FPS = 30
AUDIO_EXTS = (".mp3", ".wav", ".opus", ".ulaw", ".alaw")
IMG_PAD_BEFORE = 0.50
IMG_PAD_AFTER  = 0.50
FALLBACK_CLIP_DURATION = 3.0
SILENCE_SR = 24000

def load_plan(p: Path) -> Dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Cannot read plan: {e}", file=sys.stderr); sys.exit(1)

def make_slug(s: str, default: str = "video") -> str:
    s = (s or "").strip().lower()
    if not s: return default
    # letters, digits, hyphen, underscore only
    s = re.sub(r"[^a-z0-9_-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or default

def next_numbered_path(dir_: Path, slug: str, ext: str = ".mp4") -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    i = 1
    while True:
        p = dir_ / f"{slug}_{i:03d}{ext}"
        if not p.exists():
            return p
        i += 1

def srt_escape(text: str) -> str:
    return (text or "").replace("\n", " ").strip()

def to_srt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    hh = ms // (3600 * 1000); ms %= 3600 * 1000
    mm = ms // (60 * 1000);   ms %= 60 * 1000
    ss = ms // 1000;          ms %= 1000
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"

def clip_spoken_text(clip: Dict[str, Any]) -> str:
    if "dialogue_text" in clip or "verse" in clip:
        parts = []
        v = clip.get("verse") or {}
        if v.get("text"):
            ref = (v.get("ref") or "").strip()
            parts.append(f"{v['text']} ({ref})." if ref else v["text"])
        if clip.get("dialogue_text"):
            parts.append(clip["dialogue_text"])
        return " ".join(" ".join(parts).split())
    return " ".join(((clip.get("dialogue") or "")).split())

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

def build_video(plan_path: Path, img_dir: Path, aud_dir: Path, out_dir: Path, out_override: Path | None) -> None:
    plan = load_plan(plan_path)
    clips: List[Dict[str, Any]] = plan.get("clips", [])
    if not clips:
        print("No clips in plan.", file=sys.stderr); sys.exit(1)

    # decide output filenames
    title = plan.get("title", "")
    slug = make_slug(title, "video")
    if out_override is None:
        out_path = next_numbered_path(out_dir, slug, ".mp4")
    else:
        out_path = out_override
        out_path.parent.mkdir(parents=True, exist_ok=True)

    segments = []
    srt_rows: List[tuple[int, float, float, str]] = []
    t_cursor = 0.0

    for i, clip in enumerate(clips, start=1):
        idx = int(clip.get("index", i) or i)
        img_path = img_dir / f"clip{idx}.png"
        if not img_path.exists():
            print(f"Missing image: {img_path}", file=sys.stderr); sys.exit(1)

        aud_path = find_audio_for_clip(aud_dir, idx)

        audio_clip = None
        audio_dur = 0.0
        if aud_path:
            try:
                audio_clip = AudioFileClip(str(aud_path))
                audio_dur = float(audio_clip.duration or 0.0)
            except Exception as e:
                print(f"Warning: failed reading audio {aud_path}: {e}. Using silence.", file=sys.stderr)
                audio_clip = None
                audio_dur = 0.0

        if audio_clip and audio_dur > 0:
            img_duration = max(0.01, audio_dur + IMG_PAD_BEFORE + IMG_PAD_AFTER)
        else:
            img_duration = FALLBACK_CLIP_DURATION

        v = ImageClip(str(img_path), duration=img_duration).set_fps(FPS)
        if audio_clip:
            v = v.set_audio(audio_clip)
        segments.append(v)

        s_start = t_cursor + (IMG_PAD_BEFORE if audio_dur > 0 else 0.0)
        s_end   = s_start + (audio_dur if audio_dur > 0 else img_duration)
        s_text  = clip_spoken_text(clip)
        srt_rows.append((idx, s_start, s_end, s_text))

        t_cursor += img_duration

    final = concatenate_videoclips(segments, method="compose")
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
    lines = []
    for idx, start, end, text in srt_rows:
        lines.append(f"{idx}")
        lines.append(f"{to_srt_timestamp(start)} --> {to_srt_timestamp(end)}")
        lines.append(srt_escape(text))
        lines.append("")
    srt_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"Wrote: {out_path}")
    print(f"Wrote: {srt_path}")

def main():
    ap = argparse.ArgumentParser(description="Compose images+audio into a single MP4 from plan.json")
    ap.add_argument("--plan",   type=Path, default=PLAN_DEFAULT)
    ap.add_argument("--imgdir", type=Path, default=IMGDIR_DEFAULT)
    ap.add_argument("--audiodir", type=Path, default=AUDIODIR_DEFAULT)
    ap.add_argument("--out",    type=Path, default=None, help="Optional explicit output path")
    ap.add_argument("--outdir", type=Path, default=VIDDIR_DEFAULT, help="Directory for auto-named outputs")
    args = ap.parse_args()
    build_video(args.plan, args.imgdir, args.audiodir, args.outdir, args.out)

if __name__ == "__main__":
    main()
