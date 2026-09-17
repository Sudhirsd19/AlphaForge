# ruff: noqa: S105, S106, S311, E501
"""
AlphaForge — Upstox Automated OAuth 2.0 Authenticator with TOTP.
"""

from __future__ import annotations

import base64
import logging
import random
import string
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import pyotp
from curl_cffi import requests

logger = logging.getLogger("alphaforge.upstox.auth")


class UpstoxAuthError(RuntimeError):
    """Raised when Upstox automated OAuth authentication fails."""


class UpstoxOAuthAuthenticator:
    """Headless TOTP-based OAuth 2.0 Authenticator for Upstox."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        redirect_uri: str,
        mobile: str,
        pin: str,
        totp_key: str,
    ) -> None:
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()
        self.redirect_uri = redirect_uri.strip()
        self.mobile = mobile.strip()
        self.pin = pin.strip()
        self.totp_key = totp_key.strip().replace(" ", "").upper()

        if not all([self.api_key, self.api_secret, self.redirect_uri, self.mobile, self.pin, self.totp_key]):
            raise ValueError("All 6 Upstox credentials are required for automated authentication.")

    def _build_session(self) -> tuple[requests.Session, str]:
        req_id = "WPRO-" + "".join(random.choices(string.ascii_letters + string.digits, k=10))
        headers = {
            "accept": "*/*",
            "accept-language": "en-GB,en;q=0.9",
            "content-type": "application/json",
            "origin": "https://login.upstox.com",
            "priority": "u=1, i",
            "referer": "https://login.upstox.com/",
            "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "x-device-details": "platform=WEB|osName=Mac OS/10.15.7|osVersion=Chrome/131.0.0.0|appVersion=4.0.0|modelName=Chrome|manufacturer=Apple|uuid=3Z1IVTlV4rUUGbNp8KP0|userAgent=Upstox 3.0 Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "x-request-id": req_id,
        }
        session = requests.Session(impersonate="chrome120", headers=headers)
        return session, req_id

    def authenticate(self, max_retries: int = 3, retry_delay_seconds: float = 3.0) -> dict[str, Any]:
        """Execute full headless login and return token payload dictionary."""
        last_error: Exception | None = None

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Upstox automated login attempt {attempt}/{max_retries}...")
                session, req_id = self._build_session()

                dialog_url = (
                    f"https://api.upstox.com/v2/login/authorization/dialog"
                    f"?response_type=code&client_id={self.api_key}&redirect_uri={self.redirect_uri}"
                )
                r1 = session.get(dialog_url, allow_redirects=True)
                if r1.status_code != 200:
                    raise UpstoxAuthError(f"Dialog request failed with HTTP {r1.status_code}")

                parsed_url = urlparse(r1.url)
                qs = parse_qs(parsed_url.query)
                user_id = qs.get("user_id", [None])[0]
                internal_client_id = qs.get("client_id", [None])[0]

                if not user_id or not internal_client_id:
                    raise UpstoxAuthError(f"Failed to extract OAuth IDs from redirect URL: {r1.url}")

                r2 = session.post(
                    "https://service.upstox.com/login/open/v6/auth/1fa/otp/generate",
                    json={"data": {"mobileNumber": self.mobile, "userId": user_id}},
                )
                res2 = r2.json() if r2.status_code == 200 else {}
                if not res2.get("success"):
                    err = res2.get("error", {}).get("message", "OTP generate failed")
                    raise UpstoxAuthError(f"Step 2 OTP generation failed: {err}")

                validate_otp_token = res2["data"]["validateOTPToken"]

                totp_code = pyotp.TOTP(self.totp_key).now()
                r3 = session.post(
                    "https://service.upstox.com/login/open/v4/auth/1fa/otp-totp/verify",
                    json={"data": {"otp": totp_code, "validateOtpToken": validate_otp_token}},
                )
                res3 = r3.json() if r3.status_code == 200 else {}
                if not res3.get("success"):
                    err = res3.get("error", {}).get("message", "TOTP validation failed")
                    raise UpstoxAuthError(f"Step 3 TOTP validation failed: {err}")

                pin_b64 = base64.b64encode(self.pin.encode("utf-8")).decode("utf-8")
                params4 = {
                    "client_id": internal_client_id,
                    "redirect_uri": "https://api-v2.upstox.com/login/authorization/redirect",
                }
                r4 = session.post(
                    "https://service.upstox.com/login/open/v3/auth/2fa",
                    params=params4,
                    json={"data": {"twoFAMethod": "SECRET_PIN", "inputText": pin_b64}},
                    allow_redirects=True,
                )
                res4 = r4.json() if r4.status_code == 200 else {}
                if not res4.get("success"):
                    err = res4.get("error", {}).get("message", "PIN verification failed")
                    raise UpstoxAuthError(f"Step 4 PIN submission failed: {err}")

                params5 = {
                    "client_id": internal_client_id,
                    "redirect_uri": "https://api-v2.upstox.com/login/authorization/redirect",
                    "requestId": req_id,
                    "response_type": "code",
                }
                r5 = session.post(
                    "https://service.upstox.com/login/v2/oauth/authorize",
                    params=params5,
                    json={"data": {"userOAuthApproval": True}},
                    allow_redirects=True,
                )
                res5 = r5.json() if r5.status_code == 200 else {}
                if not res5.get("success"):
                    err = res5.get("error", {}).get("message", "OAuth approval failed")
                    raise UpstoxAuthError(f"Step 5 OAuth authorize failed: {err}")

                redirect_uri_val = res5.get("data", {}).get("redirectUri", "")
                parsed_redirect = urlparse(redirect_uri_val)
                code_list = parse_qs(parsed_redirect.query).get("code", [])
                if not code_list:
                    raise UpstoxAuthError(f"Authorization code not found in redirect URL: {redirect_uri_val}")
                auth_code = code_list[0]

                token_data = (
                    f"code={auth_code}"
                    f"&client_id={self.api_key}"
                    f"&client_secret={self.api_secret}"
                    f"&redirect_uri={self.redirect_uri}"
                    f"&grant_type=authorization_code"
                )
                token_headers = {
                    "accept": "application/json",
                    "content-type": "application/x-www-form-urlencoded",
                }
                r6 = session.post(
                    "https://api.upstox.com/v2/login/authorization/token",
                    data=token_data,
                    headers=token_headers,
                )
                res6 = r6.json() if r6.status_code == 200 else {}
                access_token = res6.get("access_token") or (
                    res6.get("data", {}).get("access_token") if isinstance(res6.get("data"), dict) else None
                )

                if not access_token:
                    raise UpstoxAuthError(f"Token exchange failed (HTTP {r6.status_code}): {res6}")

                user_name = res6.get("user_name") or res6.get("data", {}).get("user_name", "Unknown")
                logger.info(f"Upstox automated login successful for user: {user_name}")

                return {
                    "success": True,
                    "access_token": access_token,
                    "user_name": user_name,
                    "user_id": res6.get("user_id"),
                    "raw": res6,
                }

            except Exception as exc:
                last_error = exc
                logger.warning(f"Attempt {attempt} failed: {exc}")
                if attempt < max_retries:
                    time.sleep(retry_delay_seconds)

        raise UpstoxAuthError(f"Automated Upstox login failed after {max_retries} attempts: {last_error}") from last_error
