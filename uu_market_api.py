import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from uu_market_radar import DEFAULT_OUTPUT, run_radar, sample_uu_prices


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._json(200, {"ok": True})
            return
        if path == "/latest":
            if DEFAULT_OUTPUT.exists():
                self._json(200, json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8")))
            else:
                self._json(404, {"ok": False, "error": "no radar output yet"})
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/run":
            try:
                self._json(200, run_radar())
            except Exception as exc:  # noqa: BLE001 - surface API error for n8n.
                self._json(500, {"ok": False, "error": str(exc)})
            return
        if path == "/sample":
            try:
                self._json(200, sample_uu_prices())
            except Exception as exc:  # noqa: BLE001 - surface API error for systemd timer.
                self._json(500, {"ok": False, "error": str(exc)})
            return
        else:
            self._json(404, {"ok": False, "error": "not found"})
            return


def main() -> int:
    host = os.environ.get("UU_API_HOST", "127.0.0.1")
    port = int(os.environ.get("UU_API_PORT", "8765"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"UU market radar API listening on http://{host}:{port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
