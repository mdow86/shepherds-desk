#!/usr/bin/env python3
"""
make_signed_map.py
-------------------
Generates a mapping of clipN → signed Supabase Storage URLs
so Veo (or any video generator) can reference uploaded images remotely.

Usage:
    python helpers/make_signed_map.py jobs/manual-run 7 3600

Args:
    <folder>   — path inside the Supabase bucket (e.g. jobs/manual-run)
    <count>    — number of clips (clip1.png → clipN.png)
    <expires>  — link expiration seconds (e.g. 3600 = 1 hour)

Writes:
    outputs/remote_map.json
"""

import os, sys, json, pathlib
from supabase import create_client, Client
from dotenv import load_dotenv

# --- Load .env automatically from project root ---
repo_root = pathlib.Path(__file__).resolve().parents[3]
dotenv_path = repo_root / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path)
else:
    load_dotenv()  # fallback

# --- Config ---
OUTDIR = repo_root / "src" / "media_generator" / "outputs"
OUTPATH = OUTDIR / "remote_map.json"


def main() -> None:
    if len(sys.argv) < 4:
        print("Usage: make_signed_map.py <folder> <count> <expires_seconds>", file=sys.stderr)
        sys.exit(1)

    folder = sys.argv[1].strip().strip("/")
    count = int(sys.argv[2])
    expires = int(sys.argv[3])

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY")
    bucket = os.getenv("SUPABASE_BUCKET", "media")

    if not supabase_url or not supabase_key:
        print("Missing env: SUPABASE_URL and/or SUPABASE_SERVICE_KEY", file=sys.stderr)
        sys.exit(1)

    client: Client = create_client(supabase_url, supabase_key)

    mapping = {}
    for i in range(1, count + 1):
        file_path = f"{folder}/clip{i}.png"
        try:
            signed = client.storage.from_(bucket).create_signed_url(file_path, expires)
            if isinstance(signed, dict) and "signedURL" in signed:
                mapping[str(i)] = f"{supabase_url}{signed['signedURL']}"
            else:
                mapping[str(i)] = signed.get("signedUrl") or str(signed)
            print(f"[OK] {file_path}")
        except Exception as e:
            print(f"[FAIL] {file_path}: {e}", file=sys.stderr)

    OUTDIR.mkdir(parents=True, exist_ok=True)
    OUTPATH.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    print(f"\nWrote {OUTPATH} ({len(mapping)} entries)")


if __name__ == "__main__":
    main()
