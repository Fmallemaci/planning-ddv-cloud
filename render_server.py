from __future__ import annotations

import os
from http.server import ThreadingHTTPServer

import server


def main() -> None:
    server.init_db()
    host = "0.0.0.0"
    port = int(os.getenv("PORT", "8766"))
    httpd = ThreadingHTTPServer((host, port), server.Handler)
    print(f"Planning DDV disponible en puerto {port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
