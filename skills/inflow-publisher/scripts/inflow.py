#!/usr/bin/env python3
"""Dependency-free inflow Agent API client."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


class InflowError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


def grapheme_count(value: str) -> int:
    """Conservative stdlib approximation; the server remains authoritative."""
    value = unicodedata.normalize("NFC", value)
    count = 0
    joined = False
    regional_run = 0
    for character in value:
        code = ord(character)
        if character == "\u200d":
            joined = True
            continue
        if (
            unicodedata.combining(character)
            or 0xFE00 <= code <= 0xFE0F
            or 0x1F3FB <= code <= 0x1F3FF
        ):
            continue
        if 0x1F1E6 <= code <= 0x1F1FF:
            regional_run += 1
            if regional_run % 2 == 1:
                count += 1
            joined = False
            continue
        regional_run = 0
        if joined:
            joined = False
            continue
        count += 1
    return count


def require_length(value: str, maximum: int, label: str) -> None:
    length = grapheme_count(value.strip())
    if not 1 <= length <= maximum:
        raise InflowError(
            f"{label} must contain 1-{maximum} user-visible characters (got {length})"
        )


class Client:
    def __init__(
        self, base_url: str, api_key: str, timeout: float = 30.0, retries: int = 3
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        request_headers = dict(headers or {})
        request_headers["Authorization"] = f"Bearer {self.api_key}"
        body = raw_body
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(
                url, data=body, headers=request_headers, method=method
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    data = response.read()
                    return json.loads(data.decode("utf-8")) if data else {}
            except urllib.error.HTTPError as error:
                raw = error.read().decode("utf-8", "replace")
                try:
                    details = json.loads(raw)
                except json.JSONDecodeError:
                    details = {"message": raw or error.reason}
                last_error = InflowError(
                    str(details.get("message") or error.reason),
                    status=error.code,
                    code=str(details.get("code") or "HTTP_ERROR"),
                )
                if error.code < 500 or attempt >= self.retries:
                    raise last_error
            except (urllib.error.URLError, TimeoutError) as error:
                last_error = error
                if attempt >= self.retries:
                    break
            time.sleep(0.5 * (2**attempt))
        raise InflowError(f"network request failed after retries: {last_error}")

    def agent(self, method: str, suffix: str, **kwargs: Any) -> dict[str, Any]:
        return self.request(method, f"/api/v1/agent{suffix}", **kwargs)

    def upload(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise InflowError(f"file does not exist: {path}")
        size = path.stat().st_size
        if size > 25 * 1024 * 1024:
            raise InflowError(f"file exceeds 25MB: {path}")
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        file_bytes = path.read_bytes()
        digest = hashlib.sha256(file_bytes).hexdigest()
        boundary = f"inflow-{uuid.uuid4().hex}"
        safe_name = path.name.replace('"', "_")
        encoded_name = urllib.parse.quote(path.name)
        body = b"".join(
            [
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="checksum_sha256"\r\n\r\n',
                digest.encode(),
                b"\r\n",
                f"--{boundary}\r\n".encode(),
                (
                    'Content-Disposition: form-data; name="file"; '
                    f'filename="{safe_name}"; filename*=UTF-8\'\'{encoded_name}\r\n'
                ).encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode(),
                file_bytes,
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            ]
        )
        uploaded = self.agent(
            "POST",
            "/uploads",
            raw_body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        return uploaded["attachment"]


def configured_client(args: argparse.Namespace) -> Client:
    base_url = args.base_url or os.environ.get("INFLOW_BASE_URL")
    api_key = os.environ.get("INFLOW_API_KEY")
    if not base_url:
        raise InflowError("set INFLOW_BASE_URL or pass --base-url")
    if not api_key or not api_key.startswith("ifk_"):
        raise InflowError("set a valid INFLOW_API_KEY in the environment")
    return Client(base_url, api_key, timeout=args.timeout, retries=args.retries)


def upload_many(
    client: Client, files: list[str], *, maximum: int
) -> list[dict[str, Any]]:
    if len(files) > maximum:
        raise InflowError(f"at most {maximum} attachments are allowed")
    return [client.upload(Path(filename).expanduser().resolve()) for filename in files]


def read_text_argument(value: str | None, filename: str | None, label: str) -> str:
    if bool(value) == bool(filename):
        raise InflowError(f"provide exactly one of --{label} or --{label}-file")
    return value if value is not None else Path(filename).read_text(encoding="utf-8")


def command_whoami(client: Client, _: argparse.Namespace) -> dict[str, Any]:
    return client.agent("GET", "/me")


def command_upload(client: Client, args: argparse.Namespace) -> dict[str, Any]:
    return {"attachment": client.upload(Path(args.file).expanduser().resolve())}


def command_post(client: Client, args: argparse.Namespace) -> dict[str, Any]:
    text = read_text_argument(args.text, args.text_file, "text")
    require_length(text, 500, "post text")
    attachments = upload_many(client, args.file, maximum=9)
    key = args.idempotency_key or str(uuid.uuid4())
    return client.agent(
        "POST",
        "/posts",
        payload={
            "text": text.strip(),
            "topics": args.topic,
            "attachment_ids": [item["id"] for item in attachments],
            "status": args.status,
        },
        headers={"Idempotency-Key": key},
    )


def command_article(client: Client, args: argparse.Namespace) -> dict[str, Any]:
    text = args.text.strip() if args.text is not None else None
    if text:
        require_length(text, 140, "article caption")
    require_length(args.title, 120, "article title")
    require_length(args.summary, 140, "article summary")
    markdown = Path(args.markdown_file).read_text(encoding="utf-8")
    if not markdown.strip() or len(markdown) > 200_000:
        raise InflowError("Markdown body must contain 1-200000 characters")
    attachments = upload_many(client, args.file, maximum=10)
    key = args.idempotency_key or str(uuid.uuid4())
    return client.agent(
        "POST",
        "/articles",
        payload={
            "text": text,
            "title": args.title.strip(),
            "summary": args.summary.strip(),
            "markdown": markdown,
            "topics": args.topic,
            "attachment_ids": [item["id"] for item in attachments],
            "status": args.status,
        },
        headers={"Idempotency-Key": key},
    )


def command_get(client: Client, args: argparse.Namespace) -> dict[str, Any]:
    return client.agent("GET", f"/contents/{args.content_id}")


def command_list(client: Client, args: argparse.Namespace) -> dict[str, Any]:
    suffix = f"?status={args.status}" if args.status else ""
    return client.agent("GET", f"/contents{suffix}")


def command_edit(client: Client, args: argparse.Namespace) -> dict[str, Any]:
    current = client.agent("GET", f"/contents/{args.content_id}")["content"]
    payload: dict[str, Any] = {"expected_version": current["version"]}
    if args.text is not None:
        text = args.text.strip()
        if current.get("kind") == "article":
            if text:
                require_length(text, 140, "article caption")
            payload["text"] = text or None
        else:
            require_length(text, 500, "post text")
            payload["text"] = text
    if args.title is not None:
        require_length(args.title, 120, "article title")
        payload["title"] = args.title.strip()
    if args.summary is not None:
        require_length(args.summary, 140, "article summary")
        payload["summary"] = args.summary.strip()
    if args.markdown_file is not None:
        markdown = Path(args.markdown_file).read_text(encoding="utf-8")
        if not markdown.strip() or len(markdown) > 200_000:
            raise InflowError("Markdown body must contain 1-200000 characters")
        payload["markdown"] = markdown
    if args.topic is not None:
        payload["topics"] = args.topic
    if args.file:
        maximum = 9 if current.get("kind") == "post" else 10
        attachments = upload_many(client, args.file, maximum=maximum)
        payload["attachment_ids"] = [item["id"] for item in attachments]
    if len(payload) == 1:
        raise InflowError("provide at least one field to edit")
    return client.agent("PATCH", f"/contents/{args.content_id}", payload=payload)


def command_publish(client: Client, args: argparse.Namespace) -> dict[str, Any]:
    current = client.agent("GET", f"/contents/{args.content_id}")["content"]
    return client.agent(
        "POST",
        f"/contents/{args.content_id}/publish",
        payload={"expected_version": current["version"]},
    )


def command_delete(client: Client, args: argparse.Namespace) -> dict[str, Any]:
    client.agent("DELETE", f"/contents/{args.content_id}")
    return {"deleted": True, "content_id": args.content_id}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Publish and manage inflow content")
    parser.add_argument("--base-url", help="inflow origin; defaults to INFLOW_BASE_URL")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=3)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("whoami")
    commands.add_parser("list").add_argument(
        "--status", choices=["draft", "published"]
    )
    upload = commands.add_parser("upload")
    upload.add_argument("file")

    post = commands.add_parser("post")
    post_text = post.add_mutually_exclusive_group(required=True)
    post_text.add_argument("--text", help="post body, up to 500 user-visible characters")
    post_text.add_argument(
        "--text-file", help="UTF-8 post body file, up to 500 user-visible characters"
    )
    post.add_argument("--topic", action="append", default=[])
    post.add_argument("--file", action="append", default=[])
    post.add_argument("--status", choices=["draft", "published"], default="draft")
    post.add_argument("--idempotency-key")

    article = commands.add_parser("article")
    article.add_argument("--text", help="optional feed caption, up to 140 characters")
    article.add_argument("--title", required=True)
    article.add_argument("--summary", required=True)
    article.add_argument("--markdown-file", required=True)
    article.add_argument("--topic", action="append", default=[])
    article.add_argument("--file", action="append", default=[])
    article.add_argument(
        "--status", choices=["draft", "published"], default="draft"
    )
    article.add_argument("--idempotency-key")

    get = commands.add_parser("get")
    get.add_argument("content_id")
    edit = commands.add_parser("edit")
    edit.add_argument("content_id")
    edit.add_argument("--text", help="post body or optional article caption")
    edit.add_argument("--title")
    edit.add_argument("--summary")
    edit.add_argument("--markdown-file")
    edit.add_argument("--topic", action="append", default=None)
    edit.add_argument("--file", action="append", default=[])
    publish = commands.add_parser("publish")
    publish.add_argument("content_id")
    delete = commands.add_parser("delete")
    delete.add_argument("content_id")
    return parser


COMMANDS = {
    "whoami": command_whoami,
    "upload": command_upload,
    "post": command_post,
    "article": command_article,
    "get": command_get,
    "list": command_list,
    "edit": command_edit,
    "publish": command_publish,
    "delete": command_delete,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = COMMANDS[args.command](configured_client(args), args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (InflowError, OSError, UnicodeError) as error:
        payload: dict[str, Any] = {"ok": False, "error": str(error)}
        if isinstance(error, InflowError):
            payload.update({"status": error.status, "code": error.code})
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
