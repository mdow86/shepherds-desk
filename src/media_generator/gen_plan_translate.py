#!/usr/bin/env python3
"""
Translate speech fields in plan.json to a target language using deep-translator.
- Translates: clips[*].dialogue_text, clips[*].verse.text, clips[*].subtitle
- Keeps: title, image_prompt, video_motion_prompt, metadata in English
- Overwrites plan.json in place for downstream TTS

Install:
  pip install deep-translator
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path
from typing import Dict, Any, List

try:
    from deep_translator import GoogleTranslator
except Exception as e:
    print("Missing dependency: pip install deep-translator", file=sys.stderr)
    raise

LANG_TO_DEST = {
    "english":  "en",
    "spanish":  "es",
    "japanese": "ja",
}

def load_json(p: Path) -> Dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Failed reading plan: {e}", file=sys.stderr); sys.exit(1)

def save_json(p: Path, data: Dict[str, Any]) -> None:
    # ensure_ascii=False to preserve non-Latin scripts
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def collect_strings(plan: Dict[str, Any]) -> List[tuple]:
    """
    Returns list of (path_tuple, text) to translate.
    """
    out: List[tuple] = []
    for i, c in enumerate(plan.get("clips", [])):
        dlg = c.get("dialogue_text")
        if isinstance(dlg, str) and dlg.strip():
            out.append((("clips", i, "dialogue_text"), dlg))
        verse = c.get("verse") or {}
        vtext = verse.get("text")
        if isinstance(vtext, str) and vtext.strip():
            out.append((("clips", i, "verse", "text"), vtext))
        sub = c.get("subtitle")
        if isinstance(sub, str) and sub.strip():
            out.append((("clips", i, "subtitle"), sub))
    return out

def apply_translations(plan: Dict[str, Any], pairs: List[tuple], translated: List[str]) -> None:
    for (path, _src), tgt in zip(pairs, translated):
        if path[-1] == "subtitle" and isinstance(tgt, str):
            tgt = tgt[:100]
        node = plan
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = tgt

def translate_batch(texts: List[str], dest: str, chunk_size: int = 25, delay: float = 0.2) -> List[str]:
    """
    Deep-translator has internal batching, but we chunk to be gentle.
    """
    gt = GoogleTranslator(source="auto", target=dest)
    out: List[str] = []
    for i in range(0, len(texts), chunk_size):
        chunk = texts[i:i+chunk_size]
        try:
            res = gt.translate_batch(chunk)
        except Exception as e:
            print(f"[TRANSLATE] error on chunk {i}-{i+len(chunk)-1}: {e}", file=sys.stderr); sys.exit(1)
        if isinstance(res, list):
            out.extend(res)
        else:
            out.append(res)
        time.sleep(delay)
    return out

def translate_plan(plan_path: Path, target_lang: str) -> None:
    if target_lang == "english":
        print("[TRANSLATE] target=english — no changes")
        return
    dest = LANG_TO_DEST[target_lang]
    plan = load_json(plan_path)
    pairs = collect_strings(plan)
    if not pairs:
        print("[TRANSLATE] nothing to translate; leaving plan unchanged")
        return
    texts = [t for _, t in pairs]
    translated = translate_batch(texts, dest=dest)
    if len(translated) != len(texts):
        print("[TRANSLATE] mismatch in translation lengths", file=sys.stderr); sys.exit(1)
    apply_translations(plan, pairs, translated)
    save_json(plan_path, plan)
    print(f"[TRANSLATE] localized {len(pairs)} field(s) → {plan_path}")

def main() -> None:
    ap = argparse.ArgumentParser(description="Translate speech fields in plan.json")
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--language", choices=["english", "spanish", "japanese"], default="english")
    args = ap.parse_args()
    translate_plan(args.plan, args.language)

if __name__ == "__main__":
    main()
