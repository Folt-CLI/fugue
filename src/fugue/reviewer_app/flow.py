"""One-use loopback callback and manifest conversion, with no secret diagnostics."""

import asyncio
import json
import re
import secrets
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import Request, urlopen

from fugue.reviewer_app.manifest import build_manifest, start_page
from fugue.reviewer_app.store import ReviewerAppRecord, write_private

Transport = Callable[[str], Mapping[str, object]]
DELETE_HINT = (
    "The app may already exist. Delete it at https://github.com/settings/apps "
    "(or its organization's app settings) before trying again."
)


def default_transport(code: str) -> Mapping[str, object]:
    request = Request(
        f"https://api.github.com/app-manifests/{quote(code, safe='')}/conversions",
        data=b"",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "fugue"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        payload: object = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError("Invalid conversion response")
    return payload


def convert(code: str, transport: Transport = default_transport) -> tuple[int, str, str]:
    try:
        response = transport(code)
        app_id, slug, pem = response["id"], response["slug"], response["pem"]
        if (
            type(app_id) is not int
            or app_id <= 0
            or not isinstance(slug, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", slug)
            or not isinstance(pem, str)
            or not pem.strip()
        ):
            raise RuntimeError("Invalid conversion response")
        return app_id, slug, pem
    except Exception:
        raise RuntimeError(f"Reviewer app conversion failed. {DELETE_HINT}") from None


async def setup(
    repo: str,
    directory: Path,
    org: str | None,
    timeout: float,
    on_ready: Callable[[str], object],
    transport: Transport = default_transport,
) -> ReviewerAppRecord:
    state = secrets.token_urlsafe(32)
    callback: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    tasks: set[asyncio.Task[None]] = set()
    page = ""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        tasks.add(task)
        status, body = "404 Not Found", "Not found"
        try:
            request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            method, target, _ = request.split(b"\r\n", 1)[0].decode("ascii").split(" ")
            url = urlsplit(target)
            if method == "GET" and url.path == "/start" and not callback.done():
                status, body = "200 OK", page
            elif method == "GET" and url.path == "/callback" and not callback.done():
                query = parse_qs(url.query, keep_blank_values=True)
                states = query.get("state", [])
                if len(states) != 1 or not secrets.compare_digest(
                    states[0].encode(), state.encode()
                ):
                    status, body = "400 Bad Request", "Invalid callback state"
                elif "error" in query or len(query.get("code", [])) != 1 or not query["code"][0]:
                    status, body = "400 Bad Request", "Registration cancelled or failed"
                    callback.set_exception(
                        RuntimeError("Reviewer app registration cancelled or failed")
                    )
                else:
                    status, body = "200 OK", "Registration received. Return to the terminal."
                    callback.set_result(query["code"][0])
            payload = body.encode("utf-8")
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(payload)}\r\nCache-Control: no-store\r\n"
                "Connection: close\r\n\r\n".encode("ascii")
                + payload
            )
            await writer.drain()
        except (
            ValueError,
            UnicodeError,
            OSError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            TimeoutError,
        ):
            pass
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()
            tasks.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        manifest = build_manifest(
            repo, f"Fugue reviewer {repo.replace('/', '-')}", f"{base}/callback"
        )
        target = "https://github.com/settings/apps/new"
        if org:
            target = f"https://github.com/organizations/{org}/settings/apps/new"
        page = start_page(json.dumps(manifest), target, state)
        async with asyncio.timeout(timeout):
            await asyncio.to_thread(on_ready, f"{base}/start")
            code = await callback
    except TimeoutError:
        raise RuntimeError("Reviewer app setup timed out or was cancelled in the browser") from None
    finally:
        server.close()
        await server.wait_closed()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if not callback.done():
            callback.cancel()
    app_id, slug, pem = await asyncio.to_thread(convert, code, transport)
    key = directory / "private-key.pem"
    record = ReviewerAppRecord(app_id, slug, repo, str(key))
    try:
        write_private(key, pem)
        record.save(directory)
    except Exception:
        raise RuntimeError(f"Could not save reviewer app files. {DELETE_HINT}") from None
    return record
