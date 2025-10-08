# src/media_generator/orchestrate.py
from __future__ import annotations
import argparse, json, logging, subprocess, sys, time
from pathlib import Path

try:
    import paths
except Exception:
    from helpers import paths

PLAN = paths.OUTPUTS / "plan.json"
CODE = paths.CODE_ROOT  # src/media_generator

def exists_plan() -> bool:
    if not PLAN.exists(): return False
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

# ---------- builders ----------
def gloo_cmd(a: argparse.Namespace) -> list[str]:
    cmd = [sys.executable, str(CODE / "gen_llm_gloo.py")]
    if a.clips and a.clips > 0:
        cmd += ["--clips", str(a.clips)]
    return cmd

def translate_cmd(a: argparse.Namespace) -> list[str]:
    return [sys.executable, str(CODE / "gen_plan_translate.py"),
            "--plan", str(PLAN),
            "--language", a.language]

def tts_cmd(a: argparse.Namespace) -> list[str]:
    if a.tts == "piper":
        return [sys.executable, str(CODE / "gen_tts_piper.py"),
                "--plan", str(PLAN),
                "--outdir", str(paths.OUTPUTS / "audio")]
    return [sys.executable, str(CODE / "gen_tts_11labs.py"),
            "--plan", str(PLAN),
            "--outdir", str(paths.OUTPUTS / "audio"),
            "--language", a.language]

def img_cmd(a: argparse.Namespace) -> list[str]:
    if a.img == "sd":
        return [sys.executable, str(CODE / "gen_img_sd.py"),
                "--plan", str(PLAN),
                "--outdir", str(paths.OUTPUTS / "images")]
    return [sys.executable, str(CODE / "gen_img_gemini.py"),
            "--plan", str(PLAN),
            "--outdir", str(paths.OUTPUTS / "images"),
            "--style-preset", a.style_preset]

def veo_img2vid_cmd(a: argparse.Namespace) -> list[str]:
    cmd = [sys.executable, str(CODE / "gen_img2vid_veo.py"),
           "--plan", str(PLAN),
           "--imgdir", str(paths.OUTPUTS / "images"),
           "--outdir", str(paths.OUTPUTS / "video_veo"),
           "--model", a.veo_model,
           "--aspect", a.veo_aspect,
           "--style-preset", a.style_preset]
    if a.veo_negative: cmd += ["--negative", a.veo_negative]
    if a.veo_person_generation: cmd += ["--person-generation", a.veo_person_generation]
    if a.veo_api_key: cmd += ["--api-key", a.veo_api_key]
    if a.clips and a.clips > 0: cmd += ["--clips", str(a.clips)]
    if a.veo_max_per_day and a.veo_max_per_day > 0:
        cmd += ["--max-per-day", str(a.veo_max_per_day)]
    return cmd

def compose_cmd(a: argparse.Namespace, source: str) -> list[str]:
    cmd = [sys.executable, str(CODE / "gen_vid_moviepy.py"),
           "--plan", str(PLAN),
           "--source", source,
           "--imgdir", str(paths.OUTPUTS / "images"),
           "--veodir", str(paths.OUTPUTS / "video_veo"),
           "--audiodir", str(paths.OUTPUTS / "audio"),
           "--outdir", str(paths.OUTPUTS / "video")]
    if a.clips and a.clips > 0:
        cmd += ["--clips", str(a.clips)]
    return cmd


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Video generation orchestrator")
    p.add_argument("--only", choices=["gloo", "translate", "tts", "img", "img2vid", "video"])
    p.add_argument("--skip-gloo", action="store_true")
    p.add_argument("--skip-translate", action="store_true")
    p.add_argument("--skip-tts", action="store_true")
    p.add_argument("--skip-img", action="store_true")
    p.add_argument("--skip-img2vid", action="store_true")
    p.add_argument("--skip-video", action="store_true")

    # pipeline options
    p.add_argument("--tts", choices=["piper", "elevenlabs"], default="piper")
    p.add_argument("--img", choices=["sd", "gemini"], default="sd")

    # source controls the compositor behavior and whether img2vid runs
    p.add_argument("--source", choices=["images", "veo"], default="images")

    # Language for TTS
    p.add_argument("--language", choices=["english", "spanish", "japanese"], default="english")

    # Style preset for visual generators and Veo
    p.add_argument("--style-preset", choices=["storybook", "oil", "photo"], default="storybook")

    # Clip count for Gloo and Veo limiter
    p.add_argument("--clips", type=int, default=0, help="Exact number of clips (1–10). 0 uses template default.")

    # Veo settings
    p.add_argument("--veo-model", default="veo-3.0-fast-generate-001")
    p.add_argument("--veo-aspect", choices=["16:9", "9:16"], default="16:9")
    p.add_argument("--veo-negative", default="")
    p.add_argument("--veo-person-generation", choices=["", "allow_all", "allow_adult", "dont_allow"], default="")
    p.add_argument("--veo-api-key", default="")
    p.add_argument("--veo-max-per-day", type=int, default=0)

    p.add_argument("--log", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p

def main() -> None:
    a = build_parser().parse_args()
    logging.basicConfig(level=getattr(logging, a.log), format="%(message)s")

    def enabled(stage: str) -> bool:
        if a.only and stage != a.only: return False
        return not ((stage == "gloo" and a.skip_gloo) or
                    (stage == "translate" and a.skip_translate) or
                    (stage == "tts"  and a.skip_tts)  or
                    (stage == "img"  and a.skip_img)  or
                    (stage == "img2vid" and a.skip_img2vid) or
                    (stage == "video"and a.skip_video))

    logging.info("CODE=%s", paths.CODE_ROOT)

    # 1) GLOO
    if enabled("gloo"):
        run_step("GLOO", gloo_cmd(a))
    else:
        logging.info("[GLOO] skipped")

    if not exists_plan():
        logging.error("plan.json missing or empty"); sys.exit(2)
    logging.info("[PLAN] %s — Title: %s", PLAN, load_title())

    # 2) Translate
    if a.language != "english":
        if enabled("translate"): run_step("TRANSLATE", translate_cmd(a))
        else: logging.info("[TRANSLATE] skipped")
        if not exists_plan():
            logging.error("plan.json missing or empty after translate"); sys.exit(2)

    # 3) TTS
    if enabled("tts"): run_step("TTS", tts_cmd(a))
    else: logging.info("[TTS] skipped")

    # 4) IMG
    if enabled("img"): run_step("IMG", img_cmd(a))
    else: logging.info("[IMG] skipped")

    # 4.5) IMG2VID (Veo) — run only when source=veo
    if a.source == "veo":
        if enabled("img2vid"): run_step("IMG2VID", veo_img2vid_cmd(a))
        else: logging.info("[IMG2VID] skipped")

    # 5) Compose
    if enabled("video"): run_step("VIDEO", compose_cmd(a, a.source))
    else: logging.info("[VIDEO] skipped")

    logging.info("DONE")

if __name__ == "__main__":
    main()
