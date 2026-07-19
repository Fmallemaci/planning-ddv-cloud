from __future__ import annotations

import os
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

import server as planning


class RenderHandler(planning.Handler):
    def do_GET(self) -> None:
        path = urlparse(self.path).path

        if path in ("/", "/index.html"):
            self.send_file(
                planning.WEB_DIR / "index.html",
                "text/html; charset=utf-8",
            )
            return

        super().do_GET()


def run() -> None:
    planning.init_db()

    host = "0.0.0.0"
    port = int(os.environ.get("PORT", "10000"))

    httpd = ThreadingHTTPServer((host, port), RenderHandler)
    print(f"Planning DDV V5.7 activo en {host}:{port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    run()
