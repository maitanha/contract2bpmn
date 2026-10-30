"""Local LLM engine: starts the bundled llama.cpp `llama-server` with the bundled GGUF model.

Everything runs on the user's computer (CPU, offline). Layout of a packaged app:
    <resources>/llama/llama-server[.exe]   (+ its shared libraries)
    <resources>/models/*.gguf
During development the paths fall back to app/vendor/llama-<os>/... and app/models/.
"""
from __future__ import annotations

import os
import platform
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def resource_dir() -> Path:
    """Folder that holds llama/ and models/ (PyInstaller sets sys._MEIPASS)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]  # app/


def find_server() -> Path:
    exe = "llama-server.exe" if os.name == "nt" else "llama-server"
    base = resource_dir()
    for cand in [base / "llama" / exe, *sorted((base / "vendor").glob(f"llama-*/**/{exe}"))]:
        if cand.exists():
            return cand
    raise FileNotFoundError(f"{exe} not found under {base}")


def find_model() -> Path:
    models = sorted((resource_dir() / "models").glob("*.gguf"))
    if not models:
        raise FileNotFoundError(f"no .gguf model in {resource_dir() / 'models'}")
    return models[0]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Engine:
    def __init__(self, threads: int | None = None, ctx: int = 8192):
        self.threads = threads or max(2, (os.cpu_count() or 4) // 2)
        self.ctx = ctx
        self.port = free_port()
        self.proc: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 120.0) -> None:
        server, model = find_server(), find_model()
        if os.name != "nt" and not os.access(server, os.X_OK):  # archives may drop the executable bit
            os.chmod(server, 0o755)
        env = dict(os.environ)
        env["CUDA_VISIBLE_DEVICES"] = ""  # CPU only, also on machines with a GPU
        if platform.system() == "Linux":
            env["LD_LIBRARY_PATH"] = f"{server.parent}:{env.get('LD_LIBRARY_PATH', '')}"
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            [str(server), "-m", str(model), "--host", "127.0.0.1", "--port", str(self.port),
             "-c", str(self.ctx), "-t", str(self.threads), "-np", "2", "--jinja"],
            cwd=str(server.parent), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags)
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc.poll() is not None:
                raise RuntimeError("llama-server stopped while starting")
            try:
                with urllib.request.urlopen(f"{self.url}/health", timeout=2) as r:
                    if b"ok" in r.read():
                        return
            except OSError:
                pass
            time.sleep(0.5)
        raise TimeoutError("llama-server did not become ready")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
