from fastapi import FastAPI
from pydantic import BaseModel, field_validator
from enum import Enum
import os, re, uuid, tempfile, shutil
from supabase import create_client
from dotenv import load_dotenv; load_dotenv("api/.env")

class Style(str, Enum):
    storybook="storybook"; painting="painting"; realistic="realistic"

class Req(BaseModel):
    style: Style
    language: str
    topic: str
    @field_validator("language")
    @classmethod
    def lang(cls, v):
        if v not in ["English","Japanese","Spanish"]:
            raise ValueError("unsupported language")
        return v
    @field_validator("topic")
    @classmethod
    def guard(cls, v):
        v = re.sub(r"[^\x20-\x7E]+"," ", v.strip())
        if not (3 <= len(v) <= 600): raise ValueError("topic length")
        return v

app = FastAPI()
SB_URL = os.environ["SUPABASE_URL"]
SB_SERVICE = os.environ["SUPABASE_SERVICE_KEY"]  # service role, backend only
VIDEO_BUCKET = os.environ.get("VIDEO_BUCKET","videos")
sb = create_client(SB_URL, SB_SERVICE)

@app.post("/api/create-video")
def create_video(r: Req):
    # TODO: call your real generator here
    # For now, copy sample to prove the flow
    sample = os.environ.get("SAMPLE_MP4", "api/sample.mp4")
    if not os.path.exists(sample):
        return {"ok": False, "error": "Missing sample.mp4"}
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "out.mp4")
    shutil.copy(sample, out)

    name = f"{slugify(r.topic)}-{uuid.uuid4().hex[:6]}.mp4"
    with open(out, "rb") as f:
        resp = sb.storage.from_(VIDEO_BUCKET).upload(
            name, f, {"contentType":"video/mp4","cacheControl":"3600"}
        )
    if getattr(resp, "error", None):
        return {"ok": False, "error": str(resp.error)}
    return {"ok": True, "filename": name, "message": "Video created"}

def slugify(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+","-", s).strip("-")
    return s or "video"
