"""
Strict auth helper for Gloo.

Requires credentials from either:
  1) A specific .env file you pass to get_bearer_header(), or
  2) Already-present process environment variables.

No silent fallbacks. Fails fast if vars are missing.
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

import requests


def _load_env_file(path: Path | None) -> None:
    """Load key=val pairs from a .env file into os.environ (no override)."""
    if path is None:
        return
    if not path.exists():
        # If a file was explicitly provided, treat missing as an error.
        print(f".env not found: {path}", file=sys.stderr)
        sys.exit(2)
    try:
        from dotenv import dotenv_values  # type: ignore
    except Exception:
        print("python-dotenv not installed; cannot load the provided .env file", file=sys.stderr)
        sys.exit(2)
    for k, v in dotenv_values(str(path)).items():
        if v is not None and k not in os.environ:
            os.environ[k] = v


def _require(name: str) -> str:
    v = os.getenv(name)
    if not v:
        print(f"Missing required env var: {name}", file=sys.stderr)
        sys.exit(2)
    return v


def _get_access_token(client_id: str, client_secret: str) -> dict:
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    r = requests.post(
        "https://platform.ai.gloo.com/oauth2/token",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {auth}",
        },
        data={"grant_type": "client_credentials", "scope": "api/access"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def get_bearer_header(env_file: str | None = None) -> dict:
    """
    Load creds from a specific .env (if given), else rely on current env.
    Returns headers with Bearer token. Exits on error.
    """
    _load_env_file(Path(env_file) if env_file else None)
    client_id = _require("GLOO_CLIENT_ID")
    client_secret = _require("GLOO_CLIENT_SECRET")
    token = _get_access_token(client_id, client_secret)["access_token"]
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
