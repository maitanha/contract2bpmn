# PyInstaller spec for Contract2BPMN (Windows, macOS; Linux for local tests).
# Before building, place:  app/llama/   (llama-server[.exe] and its libraries, from llama.cpp release b11624)
#                          app/models/  (Qwen3.5-0.8B-Q4_K_M.gguf)
# Build:  cd app && pyinstaller --noconfirm contract2bpmn.spec
import sys
from pathlib import Path

APP = Path(SPECPATH)
SRC = APP.parent / "src"
NAME = "Contract2BPMN"

datas = [
    (str(APP / "contract2bpmn" / "ui"), "contract2bpmn/ui"),
    (str(SRC / "termground" / "schema"), "termground/schema"),
    (str(APP / "models"), "models"),
]
llama = APP / "llama"
if llama.exists():
    datas.append((str(llama), "llama"))

a = Analysis(
    [str(APP / "run_app.py")],
    pathex=[str(APP), str(SRC)],
    datas=datas,
    hiddenimports=["termground.extract", "termground.glossary", "termground.lexicon", "termground.verify",
                   "termground.compile_bpmn", "termground.validate", "termground.llm", "termground.ir"],
    excludes=["torch", "vllm", "transformers", "numpy", "scipy", "pandas", "matplotlib", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=NAME, console=False,
          icon=str(APP / "packaging" / ("icon.ico" if sys.platform == "win32" else "icon.icns"))
          if (APP / "packaging" / ("icon.ico" if sys.platform == "win32" else "icon.icns")).exists() else None)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME)
if sys.platform == "darwin":
    app = BUNDLE(coll, name=f"{NAME}.app", bundle_identifier="org.termground.contract2bpmn",
                 info_plist={"CFBundleShortVersionString": "0.1.0", "NSHighResolutionCapable": True})
