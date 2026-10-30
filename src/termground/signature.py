"""Workflow signature (plan §7): the canonical form every system is scored on, read only from BPMN XML.

parse(xml) -> Signature: per rule (start event that is not a message start), the trigger label and the
ordered tasks reachable from it (sequence flows, plus message flows that hand over to another pool).
Each step keeps: pool name, task label, numbers (from the task label and attached timer labels) and the
pools that receive message flows from the task (beneficiaries).

Gold is scored the same way: gold IR -> S5 compile -> parse. Labels are compared by cosine similarity of
sentence embeddings (BAAI/bge-large-en-v1.5) with a fixed threshold TAU (sensitivity analysis in the
paper), numbers must match exactly, pools are matched to parties by name or alias.
"""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from lxml import etree
from scipy.optimize import linear_sum_assignment

B = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
TAU = 0.75
TASKS = {"task", "userTask", "serviceTask", "sendTask", "receiveTask", "manualTask", "scriptTask",
         "businessRuleTask", "subProcess", "callActivity"}
WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
           "ten": 10, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty-eight": 48}


@dataclass
class StepSig:
    pool: str
    label: str
    numbers: frozenset
    beneficiaries: frozenset = frozenset()


@dataclass
class RuleSig:
    trigger: str
    trigger_numbers: frozenset
    steps: list[StepSig] = field(default_factory=list)


@dataclass
class Signature:
    rules: list[RuleSig]
    pools: list[str]
    valid_parse: bool = True


def numbers(text: str) -> frozenset:
    """Contract numbers in a label, typed: ('pct', v), ('usd', v), ('min', v). Bare counts are ignored.
    Durations are converted to minutes, so '2 hours' and '120 minutes' match."""
    t = text.lower().replace(",", "")
    for w, v in WORDNUM.items():
        t = re.sub(rf"\b{w}\b", str(v), t)
    out = set()
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(%|percent\b|per cent\b)", t):
        out.add(("pct", float(m.group(1))))
    for m in re.finditer(r"(?:\$|usd\s*)(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:usd|dollars?)\b", t):
        out.add(("usd", float(m.group(1) or m.group(2))))
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(business days?|minutes?|mins?|hours?|hrs?|days?)\b", t):
        u = m.group(2)
        f = 1440 if u.startswith(("day", "business")) else 60 if u.startswith(("hour", "hr")) else 1
        out.add(("min", float(m.group(1)) * f))
    return frozenset(out)


def _dur(ns: frozenset) -> frozenset:
    return frozenset(x for x in ns if x[0] == "min")


def _plain(ns: frozenset) -> frozenset:
    return frozenset(x for x in ns if x[0] != "min")


def parse(xml: str | None) -> Signature:
    if not xml:
        return Signature([], [], False)
    try:
        doc = etree.fromstring(xml.encode())
    except Exception:  # noqa: BLE001
        return Signature([], [], False)
    el = {e.get("id"): e for e in doc.iter() if e.get("id")}
    kind = {i: etree.QName(e).localname for i, e in el.items()}
    name = {i: (e.get("name") or "").strip() for i, e in el.items()}
    proc_pool = {}
    for p in doc.iter(B + "participant"):
        if p.get("processRef"):
            proc_pool[p.get("processRef")] = p.get("id")
    owner = {}
    for proc in doc.iter(B + "process"):
        pool = name.get(proc_pool.get(proc.get("id"), ""), "") or proc.get("name") or proc.get("id")
        for e in proc.iter():
            if e.get("id"):
                owner[e.get("id")] = pool
    succ: dict[str, list[str]] = {}
    for f in doc.iter(B + "sequenceFlow"):
        succ.setdefault(f.get("sourceRef"), []).append(f.get("targetRef"))
    attached: dict[str, list[str]] = {}
    for e in doc.iter(B + "boundaryEvent"):
        attached.setdefault(e.get("attachedToRef"), []).append(e.get("id"))
        succ.setdefault(e.get("attachedToRef"), []).append(e.get("id"))
    bens: dict[str, set] = {}
    for f in doc.iter(B + "messageFlow"):
        s, t = f.get("sourceRef"), f.get("targetRef")
        if kind.get(s) in TASKS:
            tgt_pool = name.get(t) if kind.get(t) == "participant" else owner.get(t, "")
            bens.setdefault(s, set()).add(tgt_pool)
        elif kind.get(t) in ("startEvent", "intermediateCatchEvent"):
            succ.setdefault(s, []).append(t)  # hand-over between pools
    msg_start = {i for i, e in el.items() if kind[i] == "startEvent" and e.find(B + "messageEventDefinition") is not None}
    starts = [i for i in el if kind[i] == "startEvent" and i not in msg_start]
    if not starts:  # fall back: any start event
        starts = [i for i in el if kind[i] == "startEvent"]
    pools = [name.get(p.get("id"), "") for p in doc.iter(B + "participant")]
    # BFS from each start; each task belongs to the start that reaches it first (shortest distance)
    best: dict[str, tuple[int, int]] = {}
    for si, s in enumerate(starts):
        dist = {s: 0}
        q = deque([s])
        while q:
            u = q.popleft()
            for v in succ.get(u, []):
                if v not in dist:
                    dist[v] = dist[u] + 1
                    q.append(v)
        for v, d in dist.items():
            if kind.get(v) in TASKS and (v not in best or d < best[v][1]):
                best[v] = (si, d)
    rules = []
    for si, s in enumerate(starts):
        tasks = sorted([t for t, (k, d) in best.items() if k == si], key=lambda t: best[t][1])
        steps = []
        for t in tasks:
            lab = name[t]
            ns = numbers(lab)
            for b in attached.get(t, []):
                ns = ns | numbers(name.get(b, ""))
            steps.append(StepSig(owner.get(t, ""), lab, ns, frozenset(bens.get(t, set()) - {owner.get(t, "")})))
        rules.append(RuleSig(name[s], numbers(name[s]), steps))
    return Signature(rules, pools, True)


# ------------------------------------------------------------------ similarity
@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer("BAAI/bge-large-en-v1.5", device="cpu")


_cache: dict[str, np.ndarray] = {}


def embed(texts: list[str]) -> np.ndarray:
    miss = [t for t in dict.fromkeys(texts) if t not in _cache]
    if miss:
        vecs = _model().encode(miss, normalize_embeddings=True, batch_size=64)
        for t, v in zip(miss, vecs):
            _cache[t] = v
    return np.stack([_cache[t] for t in texts]) if texts else np.zeros((0, 1024))


def sim(a: str, b: str) -> float:
    if not a.strip() or not b.strip():
        return 0.0
    va, vb = embed([a, b])
    return float(va @ vb)


# ------------------------------------------------------------------ party matching
def _norm(s: str) -> str:
    s = s.lower().replace("[", "").replace("]", "")
    s = re.sub(r"\b(the|a|an)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def party_of(pool: str, parties: list[dict]) -> str | None:
    """Map a pool name to a gold party id: exact match on name/alias, else token containment."""
    n = _norm(pool)
    if not n:
        return None
    for p in parties:
        for x in [p["name"], *p.get("aliases", [])]:
            if _norm(x) == n:
                return p["id"]
    toks = set(n.split())
    hits = []
    for p in parties:
        for x in [p["name"], *p.get("aliases", [])]:
            xt = set(_norm(x).split())
            if xt and (xt <= toks or toks <= xt):
                hits.append(p["id"])
                break
    return hits[0] if len(set(hits)) == 1 else None


# ------------------------------------------------------------------ comparison
def _step_eq(a: StepSig, b: StepSig, pa, pb, tau) -> bool:
    if pa(a.pool) is None or pa(a.pool) != pb(b.pool):
        return False
    if _plain(b.numbers) - _plain(a.numbers) or _plain(a.numbers) - _plain(b.numbers):
        return False
    if _dur(b.numbers) != _dur(a.numbers):
        return False
    if {pa(x) for x in a.beneficiaries} != {pb(x) for x in b.beneficiaries}:
        return False
    return sim(a.label, b.label) >= tau


def _rule_score(a: RuleSig, b: RuleSig) -> float:
    s = sim(a.trigger, b.trigger)
    if a.steps and b.steps:
        s = 0.5 * s + 0.5 * sim(a.steps[0].label, b.steps[0].label)
    return s


def compare(pred: Signature, gold: Signature, parties_pred: list[dict], parties_gold: list[dict],
            tau: float = TAU) -> dict:
    """Score pred against a reference signature (gold, or the seed output for invariance).
    Returns em (bool), step_tp, n_pred_steps, n_gold_steps, rules_ok, trigger_ok."""
    out = {"em": False, "step_tp": 0, "n_pred_steps": sum(len(r.steps) for r in pred.rules),
           "n_gold_steps": sum(len(r.steps) for r in gold.rules), "rules_ok": 0, "trigger_ok": 0,
           "n_pred_rules": len(pred.rules), "n_gold_rules": len(gold.rules)}
    if not pred.valid_parse or not pred.rules or not gold.rules:
        return out

    def pa(pool):
        return party_of(pool, parties_pred)

    def pb(pool):
        return party_of(pool, parties_gold)
    m = np.array([[_rule_score(a, b) for b in gold.rules] for a in pred.rules])
    ri, ci = linear_sum_assignment(-m)
    all_ok = len(pred.rules) == len(gold.rules)
    for i, j in zip(ri, ci):
        a, b = pred.rules[i], gold.rules[j]
        trig = sim(a.trigger, b.trigger) >= tau and _plain(b.trigger_numbers) <= _plain(a.trigger_numbers) \
            and _dur(b.trigger_numbers) <= _dur(a.trigger_numbers)
        out["trigger_ok"] += int(trig)
        tp = sum(_step_eq(x, y, pa, pb, tau) for x, y in zip(a.steps, b.steps))
        out["step_tp"] += tp
        ok = trig and len(a.steps) == len(b.steps) and tp == len(b.steps)
        out["rules_ok"] += int(ok)
        all_ok = all_ok and ok
    out["em"] = bool(all_ok)
    return out
