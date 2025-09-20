"""
Pure mappers from plan → downstream job payloads.
Supports both Plan v1 and Plan v2.

- V1 clips: { index, ..., dialogue, image_prompt }
- V2 clips: { index, ..., mode, dialogue_text?, verse{ref,text}?, image_prompt }

TTS text for V2 = [verse.text (+ref)] + dialogue_text (when present).
"""

from __future__ import annotations
from typing import List, Dict, Any

def _clip_speech_v1(clip: Dict[str, Any]) -> str:
    return (clip.get("dialogue") or "").strip()

def _clip_speech_v2(clip: Dict[str, Any]) -> str:
    parts = []
    verse = clip.get("verse") or None
    if verse and verse.get("text"):
        ref = (verse.get("ref") or "").strip()
        parts.append(f"{verse['text']} ({ref})." if ref else verse["text"])
    if clip.get("dialogue_text"):
        parts.append(clip["dialogue_text"])
    return " ".join(" ".join(parts).split())

def _clip_speech(clip: Dict[str, Any]) -> str:
    # V2 if it has dialogue_text or verse
    if ("dialogue_text" in clip) or ("verse" in clip):
        return _clip_speech_v2(clip)
    # else V1
    return _clip_speech_v1(clip)

def plan_to_image_jobs(plan: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Map each clip's image_prompt into a simple image job spec.
    """
    jobs: List[Dict[str, str]] = []
    for clip in plan.get("clips", []):
        prompt = (clip.get("image_prompt") or "").strip()
        if not prompt:
            continue
        jobs.append({
            "scene_id": f"clip{clip['index']}",
            "prompt": prompt,
            "aspect": "16:9",
        })
    return jobs

def plan_to_tts_jobs(plan: Dict[str, Any], voice: str = "default") -> List[Dict[str, str]]:
    """
    Map each clip's spoken content into a TTS job.
    """
    jobs: List[Dict[str, str]] = []
    for clip in plan.get("clips", []):
        text = _clip_speech(clip)
        if not text:
            continue
        jobs.append({
            "line_id": f"clip{clip['index']}",
            "voice": voice,
            "text": text,
        })
    return jobs

def summarize_jobs(image_jobs, tts_jobs) -> str:
    return f"images={len(image_jobs)}, tts_lines={len(tts_jobs)}"
