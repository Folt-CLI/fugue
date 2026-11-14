"""Mint reviewer app installation tokens without exposing credential diagnostics."""

import asyncio
import base64
import json
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from urllib.request import Request, urlopen

from fugue.config import ConfigError
from fugue.reviewer_app.store import ReviewerAppRecord, app_dir

Transport = Callable[[str, str, Mapping[str, str]], Mapping[str, object]]
_SETUP = "Run fugue reviewer-app setup for this repository."


def default_transport(method: str, url: str, headers: Mapping[str, str]) -> Mapping[str, object]:
    request = Request(
        url, data=b"" if method == "POST" else None, headers=dict(headers), method=method
    )
    with urlopen(request, timeout=30) as response:
        payload: object = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("Invalid reviewer app response")
    return payload


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class ReviewerTokenProvider:
    """Load one repository's reviewer app and mint a token for each review."""

    def __init__(
        self,
        repo: str,
        config_dir: Path,
        transport: Transport = default_transport,
        clock: Callable[[], float] = time.time,
    ) -> None:
        try:
            data = json.loads((app_dir(config_dir, repo) / "app.json").read_text())
            record = ReviewerAppRecord(**data)
            if (
                type(record.app_id) is not int
                or record.app_id <= 0
                or record.repo != repo
                or not isinstance(record.slug, str)
                or not record.slug
                or not isinstance(record.key_file, str)
                or not Path(record.key_file).is_file()
            ):
                raise ValueError("Invalid reviewer app record")
        except Exception:
            raise ConfigError(
                f"Reviewer app record or private key is missing or invalid. {_SETUP}"
            ) from None
        self._record = record
        self._transport = transport
        self._clock = clock

    async def token(self) -> str:
        return await asyncio.to_thread(self._mint)

    def _mint(self) -> str:
        try:
            issued = int(self._clock()) - 60
            header = _encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
            payload = _encode(
                json.dumps(
                    {"iss": self._record.app_id, "iat": issued, "exp": issued + 540}
                ).encode()
            )
            signing_input = f"{header}.{payload}"
            signed = subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", self._record.key_file],
                input=signing_input.encode("ascii"),
                capture_output=True,
                check=True,
                timeout=30,
            )
            if not signed.stdout:
                raise ValueError("Empty signature")
            jwt = f"{signing_input}.{_encode(signed.stdout)}"
            headers = {
                "Authorization": f"Bearer {jwt}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "fugue",
            }
            installation = self._transport(
                "GET", f"https://api.github.com/repos/{self._record.repo}/installation", headers
            )
            installation_id = installation["id"]
            if type(installation_id) is not int or installation_id <= 0:
                raise ValueError("Invalid installation")
            response = self._transport(
                "POST",
                f"https://api.github.com/app/installations/{installation_id}/access_tokens",
                headers,
            )
            token = response["token"]
            if not isinstance(token, str) or not token.strip():
                raise ValueError("Invalid installation token")
            return token
        except Exception:
            raise ConfigError(f"Reviewer app authentication failed. {_SETUP}") from None
