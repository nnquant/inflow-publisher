from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

SCRIPT = Path(__file__).parents[1] / "scripts" / "inflow.py"
SPEC = importlib.util.spec_from_file_location("inflow_cli", SCRIPT)
inflow = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(inflow)


class Handler(BaseHTTPRequestHandler):
    requests: ClassVar[list[dict]] = []
    base_url = ""

    def log_message(self, *_args) -> None:
        pass

    def record(self, body: bytes = b"") -> None:
        self.requests.append(
            {
                "method": self.command,
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "idempotency": self.headers.get("Idempotency-Key"),
                "body": body,
            }
        )

    def respond(self, status: int, payload: dict | None = None) -> None:
        body = json.dumps(payload or {}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self.record()
        if self.path == "/api/v1/agent/me":
            return self.respond(200, {"agent": {"id": "a1", "handle": "daily"}})
        if self.path == "/api/v1/agent/contents/c1":
            return self.respond(200, {"content": {"id": "c1", "version": 1}})
        self.respond(404, {"code": "NOT_FOUND", "message": "missing"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.record(body)
        if self.path == "/api/v1/agent/uploads/presign":
            return self.respond(
                201,
                {
                    "attachment": {"id": "f1", "url": f"{self.base_url}/stable/f1"},
                    "upload_url": f"{self.base_url}/objects/f1",
                    "upload_headers": {"Content-Type": "image/png"},
                },
            )
        if self.path == "/api/v1/agent/uploads/f1/complete":
            return self.respond(
                200,
                {
                    "attachment": {
                        "id": "f1",
                        "url": f"{self.base_url}/stable/f1",
                        "filename": "chart.png",
                    }
                },
            )
        if self.path == "/api/v1/agent/posts":
            return self.respond(201, {"content": {"id": "c1", "version": 1}})
        if self.path == "/api/v1/agent/articles":
            return self.respond(201, {"content": {"id": "a1", "version": 1}})
        if self.path == "/api/v1/agent/contents/c1/publish":
            return self.respond(200, {"content": {"id": "c1", "version": 2}})
        self.respond(404, {"code": "NOT_FOUND", "message": "missing"})

    def do_PUT(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.record(self.rfile.read(length))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


class CliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        Handler.requests = []
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        Handler.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        os.environ["INFLOW_API_KEY"] = "ifk_test-secret-never-print"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        os.environ.pop("INFLOW_API_KEY", None)

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = inflow.main(["--base-url", Handler.base_url, *args])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_whoami_and_post_idempotency(self) -> None:
        status, output, error = self.run_cli("whoami")
        self.assertEqual(status, 0, error)
        self.assertEqual(json.loads(output)["agent"]["id"], "a1")
        status, output, error = self.run_cli(
            "post",
            "--text",
            "今日数据已更新",
            "--topic",
            "日报",
            "--idempotency-key",
            "daily-2026-08-04",
        )
        self.assertEqual(status, 0, error)
        self.assertEqual(json.loads(output)["content"]["id"], "c1")
        post = next(item for item in reversed(Handler.requests) if item["path"].endswith("/posts"))
        self.assertEqual(post["idempotency"], "daily-2026-08-04")
        self.assertNotIn("ifk_test-secret", output + error)

    def test_upload_does_not_send_api_key_to_storage(self) -> None:
        work = Path.cwd() / "work" / "skill-cli-test"
        work.mkdir(parents=True, exist_ok=True, mode=0o777)
        image = work / "chart.png"
        image.write_bytes(b"not-a-real-png")
        try:
            status, output, error = self.run_cli("upload", str(image))
            self.assertEqual(status, 0, error)
            self.assertEqual(json.loads(output)["attachment"]["id"], "f1")
            put = next(item for item in reversed(Handler.requests) if item["method"] == "PUT")
            self.assertIsNone(put["authorization"])
            self.assertEqual(put["body"], b"not-a-real-png")
        finally:
            image.unlink(missing_ok=True)
            work.rmdir()

    def test_article_sends_optional_feed_caption(self) -> None:
        work = Path.cwd() / "work" / "skill-cli-article-test"
        work.mkdir(parents=True, exist_ok=True, mode=0o777)
        markdown = work / "report.md"
        markdown.write_text("# 研究结论\n\n这是完整正文。", encoding="utf-8")
        try:
            status, output, error = self.run_cli(
                "article",
                "--text",
                "这是一条附在文章卡片上方的配文",
                "--title",
                "可转债周报",
                "--summary",
                "本周市场与策略观察",
                "--markdown-file",
                str(markdown),
                "--idempotency-key",
                "article-caption-test",
            )
            self.assertEqual(status, 0, error)
            self.assertEqual(json.loads(output)["content"]["id"], "a1")
            request = next(item for item in reversed(Handler.requests) if item["path"].endswith("/articles"))
            body = json.loads(request["body"])
            self.assertEqual(body["text"], "这是一条附在文章卡片上方的配文")
            self.assertEqual(body["markdown"], "# 研究结论\n\n这是完整正文。")
        finally:
            markdown.unlink(missing_ok=True)
            work.rmdir()


if __name__ == "__main__":
    unittest.main()
