from __future__ import annotations

import os
import threading
import traceback
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
    print("Iniciando servidor", flush=True)
    host = "0.0.0.0"
    port = int(os.environ.get("PORT", "10000"))
    httpd = ThreadingHTTPServer((host, port), RenderHandler)
    print(f"Puerto abierto en {host}:{port}", flush=True)

    def initialize_schema() -> None:
        try:
            planning.init_db()
            print("Esquema validado", flush=True)
            print("Migración de datos omitida en arranque", flush=True)
        except Exception:
            print("Error validando esquema Supabase", flush=True)
            traceback.print_exc()
            httpd.shutdown()
            os._exit(1)

    threading.Thread(target=initialize_schema, name="schema-initializer", daemon=True).start()
    httpd.serve_forever()


if __name__ == "__main__":
    run()
