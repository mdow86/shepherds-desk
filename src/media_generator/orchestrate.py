# src/media_generator/orchestrate.py
from __future__ import annotations
import argparse, json, logging, subprocess, sys, time
from pathlib import Path
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())

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

def img2vid_cmd(a: argparse.Namespace) -> list[str]:
    cmd = [sys.executable, str(CODE / "gen_img2vid.py"),
           "--plan", str(PLAN),
           "--imgdir", str(paths.OUTPUTS / "images"),
           "--outdir", str(paths.OUTPUTS / "video_veo"),
           "--resolution", a.resolution,
           "--duration", str(a.duration),
           "--model", a.model]
    if a.clips and a.clips > 0:
        cmd += ["--clips", str(a.clips)]
    if a.higgs_create_endpoint:
        cmd += ["--generate-endpoint", a.higgs_create_endpoint]
    if a.higgs_status_endpoint:
        cmd += ["--status-base", a.higgs_status_endpoint]
    if a.debug_api:
        cmd += ["--debug"]
    return cmd

def compose_cmd(a: argparse.Namespace, source: str) -> list[str]:
    cmd = [sys.executable, str(CODE / "gen_vid_moviepy.py"),
           "--plan", str(PLAN),
           "--source", source,
           "--imgdir", str(paths.OUTPUTS / "images"),
           "--veodir", str(paths.OUTPUTS / "video_veo"),
           "--audiodir", str(paths.OUTPUTS / "audio"),
           "--outdir", str(paths.OUTPUTS / "video")]
    if source == "slides":
        cmd += ["--duration", str(a.duration)]
    if a.clips and a.clips > 0:
        cmd += ["--clips", str(a.clips)]
    return cmd

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Video generation orchestrator")
    p.add_argument("--only", choices=["gloo","translate","tts","img","img2vid","video"])
    p.add_argument("--skip-gloo", action="store_true")
    p.add_argument("--skip-translate", action="store_true")
    p.add_argument("--skip-tts", action="store_true")
    p.add_argument("--skip-img", action="store_true")
    p.add_argument("--skip-img2vid", action="store_true")
    p.add_argument("--skip-video", action="store_true")

    # pipeline options
    p.add_argument("--tts", choices=["piper", "elevenlabs"], default="piper")
    p.add_argument("--img", choices=["sd", "gemini"], default="sd")

    # source controls behavior
    p.add_argument("--source", choices=["video", "slides"], default="slides")

    # Language for TTS
    p.add_argument("--language", choices=["english", "spanish", "japanese"], default="english")

    # Style preset
    p.add_argument("--style-preset", choices=["storybook", "oil", "photo"], default="storybook")

    # Clip count
    p.add_argument("--clips", type=int, default=0, help="Exact number of clips (1–10). 0 uses template default.")

    # Unified generation controls
    p.add_argument("--resolution", choices=["480p", "720p", "1080p", "4K"], default="720p")
    p.add_argument("--duration", type=int, default=10, help="Slides only: per-clip seconds")
    p.add_argument("--model",
                   choices=["sora-2","higgsfield_v1","kling_25","veo_3","veo-31",
                            "higgsfield_soul","nanobanana-video","pixverse","ltxv-13b","seedance","wan-25"],
                   default="kling_25")

    # API overrides / debug
    p.add_argument("--higgs_create_endpoint", default="")
    p.add_argument("--higgs_status_endpoint", default="")
    p.add_argument("--send_form", action="store_true")
    p.add_argument("--debug_api", action="store_true")

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

    if enabled("gloo"):
        run_step("GLOO", gloo_cmd(a))
    else:
        logging.info("[GLOO] skipped")

    if not exists_plan():
        logging.error("plan.json missing or empty"); sys.exit(2)
    logging.info("[PLAN] %s — Title: %s", PLAN, load_title())

    if a.language != "english":
        if enabled("translate"): run_step("TRANSLATE", translate_cmd(a))
        else: logging.info("[TRANSLATE] skipped")
        if not exists_plan():
            logging.error("plan.json missing or empty after translate"); sys.exit(2)

    if enabled("tts"): run_step("TTS", tts_cmd(a))
    else: logging.info("[TTS] skipped")

    if enabled("img"): run_step("IMG", img_cmd(a))
    else: logging.info("[IMG] skipped")

    if a.source == "video":
        if enabled("img2vid"): run_step("IMG2VID", img2vid_cmd(a))
        else: logging.info("[IMG2VID] skipped")

    if enabled("video"): run_step("VIDEO", compose_cmd(a, a.source))
    else: logging.info("[VIDEO] skipped")

    logging.info("DONE")

if __name__ == "__main__":
    main()
