#!/usr/bin/env python3
from __future__ import annotations
import argparse, os, sys, json, glob, time, secrets
from pathlib import Path
from typing import Dict, List

try:
    from supabase import create_client, Client
except Exception as e:
    print("Missing dependency: pip install supabase", file=sys.stderr)
    raise

try:
    import paths
except Exception:
    from helpers import paths

IMGDIR_DEFAULT   = paths.OUTPUTS / "images"
OUT_DEFAULT      = paths.OUTPUTS / "remote_map.json"
BUCKET_DEFAULT   = os.getenv("SUPABASE_BUCKET", "media")
URL_DEFAULT      = os.getenv("SUPABASE_URL")
SERVICE_KEY      = os.getenv("SUPABASE_SERVICE_KEY")

def _rand_token(n: int = 8) -> str:
    return secrets.token_hex(n//2)

def _resolve_prefix(user_prefix: str | None) -> str:
    base = (user_prefix or os.getenv("JOB_PREFIX") or "jobs").strip("/ ")
    stamp = time.strftime("%Y%m%d")
    rand = _rand_token(8)
    return f"{base}/{stamp}/{rand}"

def _collect_images(imgdir: Path) -> List[Path]:
    files = sorted(Path(imgdir).glob("clip*.png"))
    if not files:
        raise SystemExit(f"No clip*.png found in {imgdir}")
    return files

def _index_from_name(p: Path) -> int:
    name = p.stem  # clip7
    try:
        return int(name.replace("clip", ""))
    except Exception:
        return 0

def main() -> None:
    ap = argparse.ArgumentParser(description="Upload outputs/images/* to Supabase (private) and return signed URLs")
    ap.add_argument("--imgdir", type=Path, default=IMGDIR_DEFAULT)
    ap.add_argument("--bucket", type=str, default=BUCKET_DEFAULT)
    ap.add_argument("--prefix", type=str, default=None, help="Remote folder prefix; default jobs/YYYYMMDD/<rand>")
    ap.add_argument("--expires", type=int, default=3600, help="Signed URL TTL seconds")
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT, help="Write mapping JSON here")
    args = ap.parse_args()

    if not URL_DEFAULT or not SERVICE_KEY:
        print("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in environment", file=sys.stderr)
        sys.exit(2)

    if not args.imgdir.exists():
        print(f"Missing images dir: {args.imgdir}", file=sys.stderr)
        sys.exit(2)

    client: Client = create_client(URL_DEFAULT, SERVICE_KEY)
    bucket = args.bucket
    prefix = _resolve_prefix(args.prefix)
    files = _collect_images(args.imgdir)

    signed_map: Dict[str, str] = {}
    for p in files:
        idx = _index_from_name(p)
        if idx <= 0:
            print(f"Skipping unexpected file name: {p.name}", file=sys.stderr)
            continue

        remote_path = f"{prefix}/{p.name}"
        # upload with upsert
        with open(p, "rb") as fh:
            res = client.storage.from_(bucket).upload(
                path=remote_path, file=fh, file_options={"content-type": "image/png", "upsert": "true"}
            )
        # create signed URL
        sres = client.storage.from_(bucket).create_signed_url(remote_path, args.expires)
        # supabase-py returns dict-like; handle common shapes:
        url = None
        if isinstance(sres, dict):
            url = sres.get("signed_url") or sres.get("signedURL")
        else:
            # Some versions return an object with attribute
            url = getattr(sres, "signed_url", None) or getattr(sres, "signedURL", None)
        if not url:
            print(f"Failed to sign {remote_path}: {sres}", file=sys.stderr)
            sys.exit(2)

        # If URL is relative, prefix with SUPABASE_URL
        if url.startswith("/"):
            url = URL_DEFAULT.rstrip("/") + url

        signed_map[str(idx)] = url
        print(f"uploaded & signed: clip{idx}.png → {remote_path}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(signed_map, indent=2), encoding="utf-8")
    print(f"Wrote signed map → {args.out.resolve()}")

if __name__ == "__main__":
    main()
