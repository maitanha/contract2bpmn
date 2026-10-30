"""Contract text -> BPMN XML with the TermGround pipeline (S1 to S5), used by the desktop app."""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

# the pipeline lives in src/termground (bundled into the app by PyInstaller)
_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from termground.extract import Pipeline  # noqa: E402
from termground.llm import GenConfig, LlamaServerLLM  # noqa: E402
from termground.validate import check  # noqa: E402

MAX_CHARS = 6000  # about one long clause; longer contracts should be split by clause


class Converter:
    def __init__(self, base_url: str):
        self.pipe = Pipeline(LlamaServerLLM(base_url=base_url, parallel=2), example_xml="", example_text="",
                             cfg=GenConfig(temperature=0.0, max_tokens=1500))

    def convert(self, text: str) -> dict:
        text = " ".join((text or "").split())
        if not text:
            return {"ok": False, "error": "Please paste a contract clause first."}
        if len(text) > MAX_CHARS:
            return {"ok": False, "error": f"The text is longer than {MAX_CHARS} characters. "
                                          "Please convert one clause (or a few related clauses) at a time."}
        t0 = time.time()
        r = self.pipe.run("TG-Full", [("contract", text)])[0]
        if r.xml is None:
            return {"ok": False, "error": "The model output could not be turned into a workflow: " + (r.error or "unknown"),
                    "seconds": round(time.time() - t0, 1)}
        chk = check(r.xml, moddle=False)
        parties = [{"name": p["name"], "type": p["type"], "aliases": p["aliases"]}
                   for p in r.stages.get("glossary", [])]
        rules = []
        for ru in r.ir.rules:
            rules.append({
                "trigger": (" or " if ru.trigger.mode == "any" else " and ").join(
                    re.sub(r"^(?:if|in case|in the event that|should)\s+", "", c.text.strip(), flags=re.I)
                    for c in ru.trigger.conditions),
                "steps": [{"step": s.step, "modality": {"O": "must", "P": "may", "F": "must not"}[s.modality],
                           "actor": r.ir.party(s.actor).name,
                           "remedy": "; ".join(a.label for a in s.actions)} for s in ru.chain]})
        return {"ok": True, "xml": r.xml, "valid": bool(chk.get("valid")), "warnings": r.stages.get("warnings", []),
                "parties": parties, "rules": rules, "seconds": round(time.time() - t0, 1)}
