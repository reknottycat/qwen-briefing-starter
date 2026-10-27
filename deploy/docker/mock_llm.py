"""Synthetic HTTPS model fixture used only by scripts/verify_docker.py."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import ssl

OUTPUT = {
    "title": "容器验证简报",
    "sections": [
        {"title": "资讯", "topic": "新闻", "text": "合成资讯已整理，项目进展仍待核实。", "source_ids": ["smoke-news"]},
        {"title": "学习", "topic": "知识", "text": "合成学习正文保留一道练习：说明水循环的三个阶段。", "source_ids": ["smoke-study"]},
    ],
    "learning_summaries": [
        {"title": "今日学习", "topic": "学习概览", "text": "今天学习水循环，并练习说明它的三个阶段。", "source_ids": ["smoke-study"]},
    ],
    "warnings": [],
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        if self.path != "/v1/chat/completions" or not 1 <= length <= 2_000_000:
            self.send_error(400)
            return
        payload = json.loads(self.rfile.read(length))
        prompt = json.dumps(payload.get("messages", []), ensure_ascii=False)
        valid = (self.headers.get("Authorization") == "Bearer " + Path("/fixture/key").read_text().strip()
                 and all(sid in prompt for sid in ("smoke-news", "smoke-study"))
                 and not payload.get("tools") and payload.get("model") == "smoke-model")
        if not valid:
            self.send_error(422)
            return
        receipt = Path("/receipt/request.json")
        count = json.loads(receipt.read_text())["requests"] + 1 if receipt.exists() else 1
        receipt.write_text(json.dumps({"requests": count, "model": payload["model"],
                                       "stream": bool(payload.get("stream")), "tools": 0,
                                       "source_ids": ["smoke-news", "smoke-study"]}))
        content = json.dumps(OUTPUT, ensure_ascii=False)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream" if payload.get("stream") else "application/json")
        self.end_headers()
        if payload.get("stream"):
            events = [
                {"id": "synthetic", "object": "chat.completion.chunk", "model": "smoke-model", "created": 1,
                 "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": None}]},
                {"id": "synthetic", "object": "chat.completion.chunk", "model": "smoke-model", "created": 1,
                 "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200}},
            ]
            for event in events:
                self.wfile.write(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            self.wfile.write(json.dumps({"id": "synthetic", "object": "chat.completion", "model": "smoke-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200}}).encode())


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 8443), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain("/fixture/model.crt", "/fixture/model.key")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
