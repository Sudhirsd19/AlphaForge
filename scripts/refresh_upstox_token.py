#!/usr/bin/env python3
# ruff: noqa: E402, E501, S603, S607
"""
AlphaForge — Daily Upstox Access Token Refresher.

Executes automated headless OAuth 2.0 login with TOTP, updates .env with the
fresh UPSTOX_ACCESS_TOKEN, and reloads the alphaforge-dashboard service.

Usage:
    python scripts/refresh_upstox_token.py [--restart-service] [--env-file PATH]
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import subprocess
import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from alphaforge.shadow_validation.upstox_auth import UpstoxOAuthAuthenticator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("alphaforge.upstox.refresh")


def load_env_dict(path: Path) -> dict[str, str]:
    env_vars: dict[str, str] = {}
    if not path.is_file():
        return env_vars
    with contextlib.suppress(Exception):
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            if k:
                env_vars[k] = v
    return env_vars


def save_env_var(path: Path, key: str, value: str) -> None:
    lines: list[str] = []
    found = False
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k == key:
                    lines.append(f'{key}="{value}"')
                    found = True
                    continue
            lines.append(line)
    if not found:
        lines.append(f'{key}="{value}"')

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with contextlib.suppress(Exception):
        path.chmod(0o600)


def restart_user_service() -> bool:
    logger.info("Restarting alphaforge-dashboard.service via systemctl --user...")
    try:
        subprocess.run(
            ["systemctl", "--user", "restart", "alphaforge-dashboard.service"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        logger.info("Successfully restarted alphaforge-dashboard.service.")
        return True
    except Exception as exc:
        logger.warning(f"Could not restart user service: {exc}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh Upstox access token automatically")
    parser.add_argument("--env-file", type=Path, default=_repo_root / ".env", help="Path to .env file")
    parser.add_argument("--restart-service", action="store_true", default=True, help="Restart alphaforge-dashboard systemd service")
    parser.add_argument("--no-restart", dest="restart_service", action="store_false", help="Skip service restart")
    args = parser.parse_args()

    env_path = args.env_file.resolve()
    env_vars = load_env_dict(env_path)
    env_vars.update(os.environ)

    api_key = env_vars.get("UPSTOX_API_KEY", "")
    api_secret = env_vars.get("UPSTOX_API_SECRET", "")
    redirect_uri = env_vars.get("UPSTOX_REDIRECT_URI", "")
    mobile = env_vars.get("UPSTOX_MOBILE", "")
    pin = env_vars.get("UPSTOX_PIN", "")
    totp_key = env_vars.get("UPSTOX_TOTP_KEY", "")

    missing = [
        k for k, v in [
            ("UPSTOX_API_KEY", api_key),
            ("UPSTOX_API_SECRET", api_secret),
            ("UPSTOX_REDIRECT_URI", redirect_uri),
            ("UPSTOX_MOBILE", mobile),
            ("UPSTOX_PIN", pin),
            ("UPSTOX_TOTP_KEY", totp_key),
        ] if not v
    ]

    if missing:
        logger.error(f"Cannot refresh token: missing required credentials in {env_path}: {missing}")
        return 1

    try:
        authenticator = UpstoxOAuthAuthenticator(
            api_key=api_key,
            api_secret=api_secret,
            redirect_uri=redirect_uri,
            mobile=mobile,
            pin=pin,
            totp_key=totp_key,
        )
        result = authenticator.authenticate(max_retries=3)
        token = result["access_token"]
        user_name = result["user_name"]

        save_env_var(env_path, "UPSTOX_ACCESS_TOKEN", token)
        logger.info(f"Updated UPSTOX_ACCESS_TOKEN in {env_path} for {user_name} (token prefix: {token[:15]}...)")

        if args.restart_service:
            restart_user_service()

        logger.info("Daily Upstox token refresh completed successfully.")
        return 0
    except Exception as exc:
        logger.error(f"Upstox token refresh failed: {exc}", exc_info=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
