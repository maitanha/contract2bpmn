"""Contract2BPMN desktop app: paste a contract clause, get a BPMN workflow (bpmn.io), save it as .bpmn.

Run (development):   python -m contract2bpmn.main            (from the app/ folder)
Browser test mode:   python -m contract2bpmn.main --serve 8765  (no window; open http://127.0.0.1:8765)
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .engine import Engine, resource_dir

UI = (resource_dir() / "contract2bpmn" / "ui") if (resource_dir() / "contract2bpmn" / "ui").exists() \
    else Path(__file__).resolve().parent / "ui"
APP_NAME = "Contract2BPMN"


class Api:
    """Methods called from the web page (window.pywebview.api.* or POST /api/*)."""

    def __init__(self):
        self.engine = Engine()
        self.converter = None
        self.status = "starting"
        self.window = None
        threading.Thread(target=self._boot, daemon=True).start()

    def _boot(self):
        try:
            self.engine.start()
            from .service import Converter
            self.converter = Converter(self.engine.url)
            self.status = "ready"
        except Exception as e:  # noqa: BLE001
            self.status = f"error: {e}"

    def get_status(self):
        return {"status": self.status}

    def convert(self, text):
        if self.converter is None:
            return {"ok": False, "error": "The language model is still starting. Please wait a moment."}
        try:
            return self.converter.convert(text)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def save_bpmn(self, xml, suggested="workflow.bpmn"):
        if self.window is None:
            return {"ok": False, "error": "no window"}
        import webview
        path = self.window.create_file_dialog(webview.SAVE_DIALOG, save_filename=suggested,
                                              file_types=("BPMN files (*.bpmn)", "All files (*.*)"))
        if not path:
            return {"ok": False, "error": "cancelled"}
        path = path if isinstance(path, str) else path[0]
        Path(path).write_text(xml, encoding="utf-8")
        return {"ok": True, "path": path}

    def open_text(self):
        if self.window is None:
            return {"ok": False}
        import webview
        paths = self.window.create_file_dialog(webview.OPEN_DIALOG, file_types=("Text files (*.txt)", "All files (*.*)"))
        if not paths:
            return {"ok": False}
        return {"ok": True, "text": Path(paths[0]).read_text(encoding="utf-8", errors="replace")}

    def shutdown(self):
        self.engine.stop()


def serve(api: Api, port: int, password: str | None = None):
    """Web mode: serve the UI and the API over HTTP on 127.0.0.1 only (reach it through SSH or VS Code port
    forwarding). With a password, every request needs HTTP basic auth (any user name, this password)."""
    import base64
    import hmac

    class H(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(UI), **kw)

        def log_message(self, *a):
            pass

        def _authorized(self) -> bool:
            if not password:
                return True
            h = self.headers.get("Authorization", "")
            if h.startswith("Basic "):
                try:
                    _, _, pw = base64.b64decode(h[6:]).decode().partition(":")
                    if hmac.compare_digest(pw, password):
                        return True
                except Exception:  # noqa: BLE001
                    pass
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Contract2BPMN"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False

        def do_GET(self):
            if self._authorized():
                super().do_GET()

        def do_POST(self):
            if not self._authorized():
                return
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            name = self.path.rsplit("/", 1)[-1]
            out = {"convert": lambda: api.convert(body.get("text", "")), "get_status": api.get_status}.get(
                name, lambda: {"ok": False, "error": "not available in browser mode"})()
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


SAMPLE = ("Lotus Inn (hereinafter the Provider) shall deliver the booked conference room. If the Provider cannot "
          "deliver the room as booked, it shall offer an equivalent room within 2 hours. If neither remedy is possible, "
          "the client is entitled to a refund of 100% of the booking fee.")


def selftest(api: Api, timeout: float = 300.0) -> int:
    """Used by the build pipeline: checks that the bundled llama-server and model work on this machine."""
    import time
    try:  # the window library must be bundled too (the GUI itself cannot open on a build server)
        import webview
        print("webview:", getattr(webview, "__version__", "ok"), flush=True)
    except Exception as e:  # noqa: BLE001
        print("webview import failed:", e, flush=True)
        return 1
    t0 = time.time()
    while api.status == "starting" and time.time() - t0 < timeout:
        time.sleep(0.5)
    print("engine:", api.status, flush=True)
    if api.status != "ready":
        return 1
    r = api.convert(SAMPLE)
    print("ok:", r.get("ok"), "valid:", r.get("valid"), "seconds:", r.get("seconds"), "error:", r.get("error"), flush=True)
    return 0 if r.get("ok") and r.get("valid") else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", type=int, default=0, help="browser test mode on this port (no window)")
    ap.add_argument("--selftest", action="store_true", help="start the model, convert a sample clause, exit 0 if valid")
    ap.add_argument("--password-file", default="", help="web mode: file that holds the password (basic auth)")
    a = ap.parse_args()
    api = Api()
    try:
        if a.selftest:
            raise SystemExit(selftest(api))
        if a.serve:
            pw = Path(a.password_file).read_text().strip() if a.password_file else None
            serve(api, a.serve, pw)
        else:
            import webview
            api.window = webview.create_window(APP_NAME, str(UI / "index.html"), js_api=api,
                                               width=1400, height=900, min_size=(1000, 650))
            webview.start()
    finally:
        api.shutdown()


if __name__ == "__main__":
    main()
