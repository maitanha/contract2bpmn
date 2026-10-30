# Contract2BPMN

Paste a contract clause, get its workflow as a BPMN 2.0 diagram.

Contract2BPMN reads penalty and remedy clauses of service contracts (for example: "if the room is not
available, the hotel shall offer another room; failing which, a refund") and draws who must do what, in
which order, as a BPMN collaboration diagram. The diagram opens in the built-in
[bpmn.io](https://bpmn.io) editor, where you can correct it and save it as a `.bpmn` file.

- **Runs on your computer.** A small language model (Qwen3.5-0.8B, 4-bit, about 0.5 GB) runs with
  [llama.cpp](https://github.com/ggml-org/llama.cpp) on the CPU. No text leaves your device and no
  internet connection is needed after installation.
- **Grounded output.** Before the model reads the clause, the tool finds the parties and their aliases
  ("Lotus Inn (hereinafter the Provider)") and normalizes modal verbs and "failing which" phrases. The
  model may only answer with these parties and a fixed list of remedy types. Numbers that are not in
  the contract are removed, and every correction is shown as a warning.
- **Check the result.** The model is small and can miss or merge remedies. Always compare the diagram
  with the contract text.

## Download

Get the installer for your system from the
[Releases](../../releases) page:

| System | File |
|---|---|
| Windows 10/11 (64-bit) | `Contract2BPMN-Setup-x64.exe` |
| macOS, Apple Silicon (M1 to M4) | `Contract2BPMN-macOS-arm64.dmg` |
| macOS, Intel | `Contract2BPMN-macOS-x64.dmg` |

The app is not signed with a paid certificate yet:
- **Windows:** if SmartScreen warns, click *More info*, then *Run anyway*.
- **macOS:** after copying the app to *Applications*, right-click it and choose *Open* the first time.

Windows needs the Microsoft Edge WebView2 runtime, which is part of Windows 10 and 11.

## Build it yourself

Every push of a tag `v*` (or a manual run of the *Build Contract2BPMN desktop app* workflow) builds all
three installers on GitHub Actions. Each build downloads a pinned llama.cpp release and the pinned
model file, checks their SHA-256 sums, builds the app with PyInstaller and runs a self-test that
converts a sample clause into a valid BPMN file.

Local development (Python 3.12):

```bash
cd app
pip install -r requirements.txt
# put llama-server (+ libraries) into app/llama/ and the .gguf model into app/models/
python -m contract2bpmn.main            # desktop window
python -m contract2bpmn.main --serve 8765   # same UI in a browser at http://127.0.0.1:8765
python -m contract2bpmn.main --selftest     # quick check without a window
```

## Licenses

- Qwen3.5-0.8B model: Apache License 2.0.
- llama.cpp: MIT License.
- bpmn-js (bpmn.io): bpmn.io license; the bpmn.io logo must stay visible in the diagram.
- This app: see `LICENSE` (to be chosen by the authors).
