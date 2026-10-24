"""Loopback-only, explicit browser QA fixture. Never imported by the application."""

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

LOCK = Lock()
STATS = {"requests": 0, "closed_streams": 0}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # Do not log prompts, keys, or document content.

    def json_response(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.json_response(200, dict(STATS))

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with LOCK:
            STATS["requests"] += 1
        user = request["messages"][-1]["content"]
        system = request["messages"][0]["content"]
        is_json = "response_format" in request
        if user == "limit":
            self.json_response(429, {"error": {"message": "QA rate limit"}})
            return
        if is_json:
            payload = json.loads(user)
            if "supporting_chunk_ids" in system:
                answerable = "保管" in payload["question"]
                text = json.dumps(
                    {
                        "answerable": answerable,
                        "reason": "本地 QA 证据判断",
                        "supporting_chunk_ids": [payload["evidence"][0]["chunk_id"]]
                        if answerable
                        else [],
                    },
                    ensure_ascii=False,
                )
            else:
                identity = payload["evidence"][0]["chunk_id"]
                if "invalid_citation" in payload["question"]:
                    identity = "invalid-qa-id"
                text = json.dumps(
                    {"claims": [{"text": "资料保管期限为三年。", "chunk_ids": [identity]}]},
                    ensure_ascii=False,
                )
            pieces = [text[i : i + 12] for i in range(0, len(text), 12)]
            delay = 0.05
        elif user in {"long", "stop"}:
            pieces = [
                f"第 {i} 段：这是本机测试服务的实时片段，用于验证滚动与取消行为。\n\n"
                for i in range(1, 91)
            ]
            delay = 0.1
        else:
            pieces = ["第一段实时内容。\n\n", "第二段实时内容。\n\n", "输出完成。"]
            delay = 0.35
        if not request.get("stream"):
            self.json_response(
                200,
                {
                    "id": "qa",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "qa",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "".join(pieces)},
                        }
                    ],
                },
            )
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            for part in pieces:
                time.sleep(delay)
                self.event(part)
            if user != "disconnect":
                self.event("", "stop")
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            with LOCK:
                STATS["closed_streams"] += 1
        finally:
            self.close_connection = True

    def event(self, text, finish=None):
        chunk = {
            "id": "qa",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "qa",
            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": finish}],
        }
        self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode())
        self.wfile.flush()


if __name__ == "__main__":
    print("Explicit QA SSE fixture: http://127.0.0.1:8766", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8766), Handler).serve_forever()
