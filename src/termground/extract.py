"""S3 Extraction and the systems compared in the paper (plan §6).

All systems end in a BPMN XML string:
  BL-XML      LLM writes BPMN XML directly (one-shot example: compiled L1).
  BL-JSON     schema-constrained JSON with free strings on the original text, as in le2026choreographed;
              converted to IR and compiled with S5.
  TG-Full     S1 glossary (LLM, enum of candidate mentions) -> S2 normalization -> S3 extraction with
              enums (party ids, remedy types) -> S4 checks -> S5 compile.
  TG-Glossary without S1 (parties are free strings).
  TG-Lexicon  without S2 (no normalization, no remedy hints).
  TG-Enum     S1 and S2, but actor and remedy are free strings.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import glossary as G
from .compile_bpmn import compile_ir
from .ir import REMEDY_PARENT, ClauseIR
from .lexicon import normalize
from .llm import LLM, GenConfig, parse_json

REMEDIES = list(REMEDY_PARENT)
UNITS = ["percent", "USD", "none"]
TUNITS = ["minute", "hour", "day", "business_day"]
SYSTEMS = ["BL-XML", "BL-JSON", "TG-Full", "TG-Glossary", "TG-Lexicon", "TG-Enum"]

REMEDY_DEF = ("Repair (fix the faulty item or service), Replacement (give an identical new item), "
              "SubstitutePerformance (provide an alternative or upgraded item or service instead of the promised one), "
              "AdditionalService (an extra free service), PriceReduction (discount, deduction or waived cost), "
              "Refund (money paid back), Damages (compensation payment or fee), ServiceCredit (credit or voucher for future use), "
              "Termination (end the contract), Other.")


# ---------------------------------------------------------------- schemas
def ir_schema(party_ids: list[str] | None, remedy_enum: bool) -> dict:
    party = {"type": "string", "enum": party_ids} if party_ids else {"type": "string"}
    remedy = {"type": "string", "enum": REMEDIES} if remedy_enum else {"type": "string"}
    num = {"anyOf": [{"type": "number"}, {"type": "null"}]}
    step = {"type": "object", "properties": {
        "actor": party,
        "modality": {"type": "string", "enum": ["O", "P", "F"]},
        "beneficiaries": {"type": "array", "maxItems": 4, "items": party},
        "actions": {"type": "array", "minItems": 1, "maxItems": 4, "items": {"type": "object", "properties": {
            "remedy": remedy, "label": {"type": "string"}, "amount": num,
            "unit": {"type": "string", "enum": UNITS}}, "required": ["remedy", "label", "amount", "unit"]}},
        "deadline_value": num,
        "deadline_unit": {"anyOf": [{"type": "string", "enum": TUNITS}, {"type": "null"}]},
    }, "required": ["actor", "modality", "beneficiaries", "actions", "deadline_value", "deadline_unit"]}
    rule = {"type": "object", "properties": {
        "trigger_mode": {"type": "string", "enum": ["all", "any"]},
        "trigger": {"type": "array", "minItems": 1, "maxItems": 3, "items": {"type": "object", "properties": {
            "text": {"type": "string"}, "threshold": num,
            "unit": {"anyOf": [{"type": "string", "enum": TUNITS}, {"type": "null"}]}},
            "required": ["text", "threshold", "unit"]}},
        "steps": {"type": "array", "minItems": 1, "maxItems": 5, "items": step},
    }, "required": ["trigger_mode", "trigger", "steps"]}
    props = {"rules": {"type": "array", "minItems": 1, "maxItems": 5, "items": rule}}
    req = ["rules"]
    if not party_ids:
        props = {"parties": {"type": "array", "minItems": 1, "maxItems": 6, "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "type": {"type": "string", "enum": ["Provider", "Customer", "Partner"]}},
            "required": ["name", "type"]}}, **props}
        req = ["parties", "rules"]
    return {"type": "object", "properties": props, "required": req}


BL_JSON_SCHEMA = {  # the plain schema of le2026choreographed: actors, trigger, ordered compensation actions
    "type": "object", "properties": {
        "actors": {"type": "array", "items": {"type": "string"}},
        "rules": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
            "trigger": {"type": "string"},
            "compensation_chain": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
                "actor": {"type": "string"}, "beneficiary": {"type": "string"}, "action": {"type": "string"}},
                "required": ["actor", "beneficiary", "action"]}}},
            "required": ["trigger", "compensation_chain"]}}},
    "required": ["actors", "rules"]}


# ---------------------------------------------------------------- prompts
EXTRACT_PROMPT = """Read the contract clause and extract its penalty rules as JSON.
A rule has a trigger (the breach that starts it) and an ordered list of remedy steps.
Step 2 applies only if step 1 fails, step 3 only if step 2 fails, and so on ("failing which").
{cue_note}A primary duty that is broken is the trigger, not a step. If the compensation escalates with a more severe breach, make a separate rule for each severity level.
modality: O = obligation (shall, or the other party is entitled to it), P = permission (may), F = prohibition.
actor: the party that gives the remedy. beneficiaries: the parties that receive it.
remedy types: {remedy_def}
amount and unit: the number in the remedy (percent or USD), else null and "none".
deadline: the time limit of the step, if any. trigger threshold: a number in the trigger (e.g. a delay of more than 60 minutes), else null.
{party_note}
Clause:
{text}
"""


def party_note(parties: list[dict]) -> str:
    rows = "\n".join(f"- {p['id']}: {p['name']} ({p['type']})" + (f", also called {', '.join(p['aliases'])}" if p["aliases"] else "")
                     for p in parties)
    return f"Parties (use the ids):\n{rows}\n"


@dataclass
class Result:
    system: str
    xml: str | None = None
    ir: ClauseIR | None = None
    raw: str = ""
    stages: dict = field(default_factory=dict)
    error: str = ""


# ---------------------------------------------------------------- conversion
def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40] or "condition"


def to_ir(obj: dict, clause_id: str, parties: list[dict] | None) -> ClauseIR:
    """Map an LLM JSON object to the IR. Free-string parties get ids in order of appearance."""
    if parties is None:
        parties = []
        names = {}

        def pid(name):
            key = (name or "unknown").strip()
            if key.lower() not in names:
                names[key.lower()] = f"P{len(names) + 1}"
                typ = next((p.get("type", "Provider") for p in obj.get("parties", [])
                            if p.get("name", "").strip().lower() == key.lower()), "Customer" if names else "Provider")
                parties.append({"id": names[key.lower()], "name": key, "type": typ, "aliases": []})
            return names[key.lower()]
        for p in obj.get("parties", []):
            pid(p.get("name", ""))
    else:
        ids = {p["id"] for p in parties}

        def pid(x):
            if x in ids:
                return x
            raise ValueError(f"unknown party {x}")
    rules = []
    for ri, r in enumerate(obj["rules"], start=1):
        conds = []
        for c in r["trigger"]:
            thr = c.get("threshold")
            conds.append({"text": c["text"], "var": _slug(c["text"]), "op": ">" if thr is not None else "=",
                          "value": thr if thr is not None else True, "unit": c.get("unit")})
        chain = []
        for k, s in enumerate(r["steps"], start=1):
            actor = pid(s["actor"])
            bens = [pid(b) for b in s.get("beneficiaries", []) if b]
            bens = list(dict.fromkeys(b for b in bens if b != actor)) or []
            share = 1.0 / len(bens) if bens else 1.0
            acts = [{"remedy": a["remedy"] if a["remedy"] in REMEDY_PARENT else "Other", "label": a["label"],
                     "amount": a.get("amount"), "unit": None if a.get("unit") in (None, "none") else a["unit"]}
                    for a in s["actions"]]
            dl = None
            if s.get("deadline_value") is not None and s.get("deadline_unit"):
                dl = {"value": s["deadline_value"], "unit": s["deadline_unit"], "anchor": None}
            chain.append({"step": k, "modality": s.get("modality", "O"), "actor": actor,
                          "beneficiaries": [{"party": b, "share": share} for b in bens], "actions": acts, "deadline": dl})
        rules.append({"rule_id": f"R{ri}", "trigger": {"mode": r.get("trigger_mode", "all"), "conditions": conds},
                      "chain": chain})
    return ClauseIR.model_validate({"clause_id": clause_id, "status": "predicted", "parties": parties, "rules": rules})


def bl_json_to_ir(obj: dict, clause_id: str) -> ClauseIR:
    conv = {"parties": [{"name": a, "type": "Provider"} for a in obj.get("actors", [])], "rules": []}
    for r in obj["rules"]:
        conv["rules"].append({"trigger_mode": "all", "trigger": [{"text": r["trigger"], "threshold": None, "unit": None}],
                              "steps": [{"actor": s["actor"], "modality": "O", "beneficiaries": [s["beneficiary"]],
                                         "actions": [{"remedy": "Other", "label": s["action"], "amount": None, "unit": "none"}],
                                         "deadline_value": None, "deadline_unit": None}
                                        for s in r["compensation_chain"]]})
    return to_ir(conv, clause_id, None)


def extract_bpmn_xml(text: str) -> str | None:
    m = re.search(r"<\?xml.*?</(?:bpmn:)?definitions>|<(?:bpmn:)?definitions.*?</(?:bpmn:)?definitions>", text, re.S)
    return m.group(0) if m else None


# ---------------------------------------------------------------- runner
class Pipeline:
    def __init__(self, llm: LLM, example_xml: str, example_text: str, cfg: GenConfig | None = None):
        self.llm, self.cfg = llm, cfg or GenConfig()
        self.example_xml, self.example_text = example_xml, example_text

    # S1 for a batch of texts
    def glossaries(self, texts: list[str]) -> list[G.Glossary]:
        """S1: rules find names, defined terms and role nouns; a constrained LLM call only types the names."""
        def typer(text, names):
            out = self.llm.chat([G.NAME_TYPE_PROMPT.format(text=text, names=json.dumps(names))],
                                G.name_type_schema(names), GenConfig(temperature=0.0, max_tokens=400))
            obj = parse_json(out[0][0]) or {}
            return {d["name"]: d["type"] for d in obj.get("names", []) if d.get("name") in names}
        return [G.hybrid_glossary(t, typer) for t in texts]

    def run(self, system: str, items: list[tuple[str, str]]) -> list[Result]:
        """items: (clause_id, text). Returns one Result per item."""
        ids, texts = [i for i, _ in items], [t for _, t in items]
        res = [Result(system=system) for _ in items]
        if system == "BL-XML":
            prompts = [("Convert the contract clause into a BPMN 2.0 collaboration diagram (XML with pools, tasks, "
                        "exclusive gateways for each 'failing which' fallback, and a BPMNDiagram section) that opens on bpmn.io.\n\n"
                        f"Example clause:\n{self.example_text}\n\nExample answer:\n{self.example_xml}\n\n"
                        f"Clause:\n{t}\n\nAnswer with the XML only.") for t in texts]
            gens = self.llm.chat(prompts, None, GenConfig(temperature=self.cfg.temperature, max_tokens=6000))
            for r, g in zip(res, gens):
                r.raw = g[0]
                r.xml = extract_bpmn_xml(g[0])
                if r.xml is None:
                    r.error = "no xml"
            return res
        if system == "BL-JSON":
            prompts = [("Extract the penalty rule from the contract clause: the actors, the trigger condition and the ordered "
                        f"compensation chain (each action applies if the previous one fails).\n\nClause:\n{t}\n\nAnswer in JSON.")
                       for t in texts]
            gens = self.llm.chat(prompts, BL_JSON_SCHEMA, self.cfg)
            for r, g, cid in zip(res, gens, ids):
                r.raw = g[0]
                self._finish(r, lambda: bl_json_to_ir(parse_json(g[0]), cid))
            return res

        use_s1 = system != "TG-Glossary"
        use_s2 = system != "TG-Lexicon"
        use_enum = system != "TG-Enum"
        work = list(texts)
        parties: list[list[dict] | None] = [None] * len(items)
        if use_s1:
            gl = self.glossaries(work)
            for i, g in enumerate(gl):
                parties[i] = g.parties
                work[i] = G.apply(work[i], g)
                res[i].stages["glossary"] = g.parties
        hints = [[] for _ in items]
        if use_s2:
            for i in range(len(work)):
                n = normalize(work[i])
                work[i] = n.text
                hints[i] = n.remedy_hints
                res[i].stages["normalized"] = n.text
        prompts, schemas = [], []
        for i in range(len(items)):
            cue = ("The cue [FALLBACK] marks the start of the next step in a chain. " if use_s2 else "")
            pn = party_note(parties[i]) if parties[i] else "List the parties first.\n"
            # remedy hints are not put in the prompt: the 0.8B model copied hint words as remedy labels (dev run)
            prompts.append(EXTRACT_PROMPT.format(cue_note=cue, remedy_def=REMEDY_DEF, party_note=pn, text=work[i]))
            pids = [p["id"] for p in parties[i]] if (parties[i] and use_enum) else None
            schema = ir_schema(pids, remedy_enum=use_enum)
            if parties[i] and not use_enum:  # free strings but parties known: ask for names
                schema = ir_schema(None, remedy_enum=False)
            schemas.append(schema)
        gens = self.llm.chat(prompts, schemas, self.cfg)
        for i, (r, g, cid) in enumerate(zip(res, gens, ids)):
            r.raw = g[0]
            obj = parse_json(g[0])
            if parties[i] and use_enum:
                self._finish(r, lambda: to_ir(obj, cid, parties[i]), source=texts[i])
            elif parties[i]:  # TG-Enum: map free party strings to glossary ids by name/alias match
                self._finish(r, lambda: to_ir(_map_free_parties(obj, parties[i]), cid, parties[i]), source=texts[i])
            else:
                self._finish(r, lambda: to_ir(obj, cid, None), source=texts[i])
        return res

    @staticmethod
    def _finish(r: Result, make, source: str | None = None):
        try:
            r.ir = make()
            from .verify import check_ir, dedupe, default_beneficiaries, ground_numbers, merge_fallback_rules
            # S4 (TermGround systems only): numbers must occur in the clause; fallback "rules" join their chain
            w = (dedupe(r.ir) + ground_numbers(r.ir, source) + merge_fallback_rules(r.ir) + default_beneficiaries(r.ir)) \
                if source is not None else []
            r.stages["warnings"] = w + check_ir(r.ir)
            r.xml = compile_ir(r.ir)
        except Exception as e:  # noqa: BLE001  (invalid output counts as wrong, plan §7)
            r.error = f"{type(e).__name__}: {e}"[:300]


def _map_free_parties(obj: dict, parties: list[dict]) -> dict:
    """TG-Enum: free-string actor names are matched to glossary parties by exact name or alias, else kept as new."""
    look = {}
    for p in parties:
        for n in [p["name"], *p["aliases"], p["id"]]:
            look[n.lower()] = p["id"]
    extra = []

    def m(x):
        k = (x or "").strip().lower()
        if k in look:
            return look[k]
        extra.append(x)
        return x
    obj = dict(obj)
    for r in obj.get("rules", []):
        for s in r.get("steps", []):
            s["actor"] = m(s.get("actor"))
            s["beneficiaries"] = [m(b) for b in s.get("beneficiaries", [])]
    if extra:
        raise ValueError(f"party not in glossary: {extra[:2]}")
    return obj


def load_example(root: Path) -> tuple[str, str]:
    import json
    seeds = [json.loads(l) for l in (root / "data/seeds/clauses.jsonl").read_text().splitlines()]
    l1 = next(s for s in seeds if s["id"] == "L1")["text"]
    xml = (root / "experiments/artifacts/gold_bpmn/L1.bpmn").read_text()
    return l1, xml
