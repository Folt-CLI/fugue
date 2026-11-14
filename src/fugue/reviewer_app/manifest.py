"""GitHub App manifest and browser registration form."""

from html import escape
from urllib.parse import urlencode


def build_manifest(repo: str, name: str, redirect_url: str) -> dict[str, object]:
    return {
        "name": name,
        "url": f"https://github.com/{repo}",
        "redirect_url": redirect_url,
        "public": False,
        "default_permissions": {"pull_requests": "write", "contents": "read"},
    }


def start_page(manifest_json: str, target_url: str, state: str) -> str:
    action = escape(f"{target_url}?{urlencode({'state': state})}", quote=True)
    manifest = escape(manifest_json, quote=True)
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        "<title>Register reviewer app</title></head><body>"
        f'<form id="register" method="post" action="{action}">'
        f'<input type="hidden" name="manifest" value="{manifest}">'
        '<button type="submit">Register reviewer app on GitHub</button></form>'
        '<script>document.getElementById("register").submit();</script></body></html>'
    )
