#!/usr/bin/env python3
"""
Compose into MP4.

--source images: slideshow from outputs/images + TTS timing
--source veo: normalize Veo MP4s, stitch, mute native audio, overlay ElevenLabs per-clip audio,
              extend short clips by freezing last frame to fit audio + padding
Writes .srt.

Now supports --clips to limit to the first N clips in plan.json.
"""
from __future__ import annotations

import argparse, json, re, sys, subprocess
from pathlib import Path
from typing import List, Dict, Any

import numpy as np
from moviepy.editor import (
    ImageClip, AudioFileClip, VideoFileClip,
    concatenate_videoclips, CompositeAudioClip
)
from moviepy.audio.AudioClip import AudioArrayClip  # noqa: F401  (import kept for compatibility)

try:
    import paths
except Exception:
    from helpers import paths

# -------- defaults --------
PLAN_DEFAULT     = paths.OUTPUTS / "plan.json"
IMGDIR_DEFAULT   = paths.OUTPUTS / "images"
VEODIR_DEFAULT   = paths.OUTPUTS / "video_veo"
AUDIODIR_DEFAULT = paths.OUTPUTS / "audio"
VIDDIR_DEFAULT   = paths.OUTPUTS / "video"

FPS = 30
AUDIO_EXTS = (".mp3", ".wav", ".opus", ".ulaw", ".alaw")
PAD_BEFORE = 0.50
PAD_AFTER  = 0.50
FALLBACK_CLIP_DURATION = 3.0

# -------- helpers --------
def shell(cmd: list[str]) -> None:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError(f"cmd failed: {' '.join(cmd)}\n{p.stderr.decode('utf-8', 'ignore')}")

def load_plan(p: Path) -> Dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Cannot read plan: {e}", file=sys.stderr); sys.exit(1)

def make_slug(s: str, default: str = "video") -> str:
    s = (s or "").strip().lower()
    if not s: return default
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

def find_audio_for_clip(aud_dir: Path, idx: int) -> Path | None:
    for ext in AUDIO_EXTS:
        p = aud_dir / f"clip{idx}{ext}"
        if p.exists():
            return p
    return None

def safe_video_open(path: Path) -> VideoFileClip:
    """
    Try to open video. If MoviePy can't read frames, normalize with ffmpeg and retry.
    Normalization: H.264 yuv420p, 30 fps, AAC, +faststart.
    """
    try:
        v = VideoFileClip(str(path))
        _ = v.get_frame(0)
        return v
    except Exception:
        norm = path.with_suffix(".norm.mp4")
        try:
            ffmpeg = "ffmpeg"
            cmd = [
                ffmpeg, "-y", "-i", str(path),
                "-vf", f"scale=trunc(iw/2)*2:trunc(ih/2)*2,fps={FPS}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high",
                "-movflags", "+faststart",
                "-c:a", "aac", "-b:a", "128k",
                str(norm)
            ]
            shell(cmd)
            v2 = VideoFileClip(str(norm))
            _ = v2.get_frame(0)
            return v2
        except Exception as e:
            raise RuntimeError(f"Failed to normalize Veo clip: {path}\n{e}")

def freeze_extend(video: VideoFileClip, target_duration: float, fps: int) -> VideoFileClip:
    cur = float(video.duration or 0.0)
    if cur >= target_duration:
        try:
            return video.subclip(0, target_duration)
        except Exception:
            pass

    pad = max(0.0, target_duration - max(0.0, cur))
    frame_t = max(0.0, (cur - (1.0 / fps)) if cur > 0 else 0.0)

    still_frame = None
    try:
        still_frame = video.get_frame(frame_t)
    except Exception:
        try:
            still_frame = video.get_frame(0)
        except Exception:
            pass

    if still_frame is None:
        still_frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    still = ImageClip(still_frame, duration=pad).set_fps(fps)
    try:
        return concatenate_videoclips([video, still], method="compose")
    except Exception:
        return ImageClip(still_frame, duration=target_duration).set_fps(fps)

# -------- core builders --------
def slice_clips(clips: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    if limit and limit > 0:
        return clips[:limit]
    return clips

def build_video_images(plan_path: Path, img_dir: Path, aud_dir: Path, out_dir: Path, out_override: Path | None, limit: int) -> None:
    plan = load_plan(plan_path)
    clips: List[Dict[str, Any]] = slice_clips(plan.get("clips", []), limit)
    if not clips:
        print("No clips in plan.", file=sys.stderr); sys.exit(1)

    title = plan.get("title", "")
    slug = make_slug(title, "video")
    out_path = out_override if out_override else next_numbered_path(out_dir, slug, ".mp4")
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

        duration = max(0.01, (audio_dur + PAD_BEFORE + PAD_AFTER) if audio_dur > 0 else FALLBACK_CLIP_DURATION)
        v = ImageClip(str(img_path), duration=duration).set_fps(FPS)
        if audio_clip:
            v = v.set_audio(audio_clip.set_start(PAD_BEFORE))
        segments.append(v)

        s_start = t_cursor + (PAD_BEFORE if audio_dur > 0 else 0.0)
        s_end   = s_start + (audio_dur if audio_dur > 0 else duration)
        s_text  = clip_spoken_text(clip)
        srt_rows.append((i, s_start, s_end, s_text))  # re-number 1..N
        t_cursor += duration

    final = concatenate_videoclips(segments, method="compose")
    final.write_videofile(str(out_path), fps=FPS, codec="libx264", audio_codec="aac",
                          threads=0, preset="medium", bitrate="4000k")

    srt_path = out_path.with_suffix(".srt")
    lines = []
    for i, start, end, text in srt_rows:
        lines += [f"{i}", f"{to_srt_timestamp(start)} --> {to_srt_timestamp(end)}", srt_escape(text), ""]
    srt_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote: {out_path}")
    print(f"Wrote: {srt_path}")

def build_video_veo(plan_path: Path, veo_dir: Path, aud_dir: Path, out_dir: Path, out_override: Path | None, limit: int) -> None:
    plan = load_plan(plan_path)
    clips: List[Dict[str, Any]] = slice_clips(plan.get("clips", []), limit)
    if not clips:
        print("No clips in plan.", file=sys.stderr); sys.exit(1)

    title = plan.get("title", "")
    slug = make_slug(title, "video")
    out_path = out_override if out_override else next_numbered_path(out_dir, slug, ".mp4")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    segments = []
    srt_rows: List[tuple[int, float, float, str]] = []
    t_cursor = 0.0

    for i, clip in enumerate(clips, start=1):
        idx = int(clip.get("index", i) or i)
        vid_path = veo_dir / f"clip{idx}.mp4"
        if not vid_path.exists():
            print(f"Missing Veo clip: {vid_path}", file=sys.stderr); sys.exit(1)

        try:
            vclip = safe_video_open(vid_path).set_fps(FPS)
        except Exception as e:
            print(f"Normalize/open failed for {vid_path}: {e}", file=sys.stderr); sys.exit(1)

        base_dur = float(vclip.duration or 0.0)

        aud_path = find_audio_for_clip(aud_dir, idx)
        audio_clip = None
        audio_dur = 0.0
        if aud_path:
            try:
                audio_clip = AudioFileClip(str(aud_path))
                audio_dur = float(audio_clip.duration or 0.0)
            except Exception as e:
                print(f"Warning: failed reading audio {aud_path}: {e}. Using native/no audio.", file=sys.stderr)
                audio_clip = None
                audio_dur = 0.0

        target_duration = base_dur
        if audio_clip and audio_dur > 0:
            target_duration = max(base_dur, audio_dur + PAD_BEFORE + PAD_AFTER)

        vclip2 = freeze_extend(vclip, target_duration, FPS)

        if audio_clip and audio_dur > 0:
            voice = audio_clip.set_start(PAD_BEFORE)
            vclip2 = vclip2.set_audio(CompositeAudioClip([voice]))
        else:
            vclip2 = vclip2.set_audio(None)

        segments.append(vclip2)

        s_start = t_cursor + (PAD_BEFORE if audio_dur > 0 else 0.0)
        s_end   = s_start + (audio_dur if audio_dur > 0 else target_duration)
        s_text  = clip_spoken_text(clip)
        srt_rows.append((i, s_start, s_end, s_text))  # re-number 1..N
        t_cursor += target_duration

    final = concatenate_videoclips(segments, method="compose")
    final.write_videofile(str(out_path), fps=FPS, codec="libx264", audio_codec="aac",
                          threads=0, preset="medium", bitrate="5000k")

    srt_path = out_path.with_suffix(".srt")
    lines = []
    for i, start, end, text in srt_rows:
        lines += [f"{i}", f"{to_srt_timestamp(start)} --> {to_srt_timestamp(end)}", srt_escape(text), ""]
    srt_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote: {out_path}")
    print(f"Wrote: {srt_path}")

# -------- CLI --------
def main():
    ap = argparse.ArgumentParser(description="Compose final video from plan.json")
    ap.add_argument("--plan",       type=Path, default=PLAN_DEFAULT)
    ap.add_argument("--source",     type=str, choices=["images", "veo"], default="images")
    ap.add_argument("--imgdir",     type=Path, default=IMGDIR_DEFAULT)
    ap.add_argument("--veodir",     type=Path, default=VEODIR_DEFAULT)
    ap.add_argument("--audiodir",   type=Path, default=AUDIODIR_DEFAULT)
    ap.add_argument("--out",        type=Path, default=None, help="Explicit output path")
    ap.add_argument("--outdir",     type=Path, default=VIDDIR_DEFAULT, help="Directory for auto-named outputs")
    ap.add_argument("--clips",      type=int, default=0, help="If >0, only use first N clips from plan.json")
    args = ap.parse_args()

    if args.source == "images":
        build_video_images(args.plan, args.imgdir, args.audiodir, args.outdir, args.out, args.clips)
    else:
        build_video_veo(args.plan, args.veodir, args.audiodir, args.outdir, args.out, args.clips)

if __name__ == "__main__":
    main()
