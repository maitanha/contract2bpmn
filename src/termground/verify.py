"""S4 Verify: logic checks on an IR. Returns warnings shown to the user (plan §4.3).

Structural errors (unknown party, bad step numbering, shares) are already rejected by the IR schema.
These checks catch IRs that are well formed but suspicious.
"""
from __future__ import annotations

from .ir import ClauseIR


def check_ir(ir: ClauseIR) -> list[str]:
    w = []
    for r in ir.rules:
        for c in r.trigger.conditions:
            if not c.text.strip():
                w.append(f"Rule {r.rule_id[1:]}: the trigger is empty")
        seen = set()
        for s in r.chain:
            key = tuple((a.remedy, a.label.strip().lower()) for a in s.actions)
            if key in seen:
                w.append(f"Rule {r.rule_id[1:]}, step {s.step}: repeats an earlier remedy")
            seen.add(key)
            for a in s.actions:
                if not a.label.strip():
                    w.append(f"Rule {r.rule_id[1:]}, step {s.step}: a remedy has no description")
                if a.unit == "percent" and a.amount is not None and not (0 < a.amount <= 1000):
                    w.append(f"Rule {r.rule_id[1:]}, step {s.step}: the percentage {a.amount:g} looks wrong")
            if s.deadline and s.deadline.value is not None and s.deadline.value <= 0:
                w.append(f"Rule {r.rule_id[1:]}, step {s.step}: the deadline is not positive")
            if not s.beneficiaries and s.modality == "O":
                w.append(f"Rule {r.rule_id[1:]}, step {s.step}: no party receives this remedy")
            if any(b.party == s.actor for b in s.beneficiaries) and s.modality == "O":
                w.append(f"Rule {r.rule_id[1:]}, step {s.step}: the party that gives the remedy also receives it")
    return w


# ---------------------------------------------------------------- number grounding (part of S4)
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
          "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "half": 0.5}


def text_numbers(text: str) -> set[float]:
    """Numbers stated in the clause, with common unit conversions (hours <-> minutes, days <-> hours)."""
    import re
    nums = {float(x.replace(",", "")) for x in re.findall(r"\d+(?:[.,]\d+)?", text)}
    nums |= {float(v) for w, v in _WORDS.items() if re.search(rf"\b{w}\b", text, re.IGNORECASE)}
    conv = set()
    for n in nums:
        conv |= {n * 60, n / 60, n * 24, n / 24}
    return nums | conv


def _near_label(v: float, label: str, text: str) -> bool:
    """The amount appears in the remedy label, or in a sentence of the clause that shares words with the label."""
    import re
    num = f"{v:g}"
    if re.search(rf"(?<![\d.]){re.escape(num)}(?![\d])", label):
        return True
    words = lambda x: {t for t in re.findall(r"[a-z]{4,}", x.lower())}  # noqa: E731
    lw = words(label)
    for sent in re.split(r"(?<=[.;])\s+", text):
        if re.search(rf"(?<![\d.]){re.escape(num)}(?![\d])", sent) and lw and len(lw & words(sent)) / len(lw) >= 0.3:
            return True
    return False


def default_beneficiaries(ir: ClauseIR) -> list[str]:
    """An obligation without a beneficiary goes to the customer when the clause has exactly one customer."""
    from .ir import Beneficiary
    customers = [p.id for p in ir.parties if p.type == "Customer"]
    w = []
    if len(customers) != 1:
        return w
    for r in ir.rules:
        for s in r.chain:
            if s.modality == "O" and not s.beneficiaries and s.actor != customers[0]:
                s.beneficiaries = [Beneficiary(party=customers[0], share=1.0)]
                w.append(f"Rule {r.rule_id[1:]}, step {s.step}: assumed the remedy goes to {ir.party(customers[0]).name}")
    return w


def ground_numbers(ir: ClauseIR, text: str) -> list[str]:
    """Remove numbers that do not occur in the clause (amounts, thresholds, deadlines). Returns warnings."""
    ok = text_numbers(text)
    good = lambda v: any(abs(v - n) < 1e-6 for n in ok)  # noqa: E731
    w = []
    for r in ir.rules:
        for c in r.trigger.conditions:
            if isinstance(c.value, (int, float)) and not isinstance(c.value, bool) and not good(float(c.value)):
                w.append(f"Rule {r.rule_id[1:]}: ignored the number {c.value:g} in the trigger, because it is not in the contract")
                c.op, c.value, c.unit = "=", True, None
        for s in r.chain:
            for a in s.actions:
                if a.amount is not None and not good(float(a.amount)):
                    w.append(f"Rule {r.rule_id[1:]}, step {s.step}: ignored the amount {a.amount:g}, because it is not in the contract")
                    a.amount, a.unit = None, None
                if a.amount is not None and not _near_label(a.amount, a.label, text):
                    w.append(f"Rule {r.rule_id[1:]}, step {s.step}: ignored the amount {a.amount:g}, because the contract states it for another remedy")
                    a.amount, a.unit = None, None
                if a.amount is not None and a.unit in ("percent", "USD"):  # the unit must match how the clause states it
                    import re
                    v = f"{a.amount:g}"
                    pct = re.search(rf"(?<![\d.]){re.escape(v)}\s*(?:%|percent)", text)
                    usd = re.search(rf"(?:\$\s*{re.escape(v)}(?![\d])|{re.escape(v)}\s*(?:USD|dollars))", text)
                    if a.unit == "USD" and pct and not usd:
                        a.unit = "percent"
                        w.append(f"Rule {r.rule_id[1:]}, step {s.step}: the amount {v} is a percentage in the contract")
                    elif a.unit == "percent" and usd and not pct:
                        a.unit = "USD"
                        w.append(f"Rule {r.rule_id[1:]}, step {s.step}: the amount {v} is in dollars in the contract")
                if a.amount is None:  # recover a stated amount from the label ("50% discount", "$300 fee")
                    import re
                    m = re.search(r"(\d+(?:\.\d+)?)\s*%", a.label) or re.search(r"\$\s*(\d+(?:\.\d+)?)", a.label)
                    if m and good(float(m.group(1))):
                        a.amount = float(m.group(1))
                        a.unit = "percent" if "%" in m.group(0) else "USD"
                        if w and w[-1].startswith(f"Rule {r.rule_id[1:]}, step {s.step}: ignored the amount"):
                            w[-1] += f"; used {m.group(0).strip()} from the remedy text instead"
            if s.deadline and s.deadline.value is not None and not good(float(s.deadline.value)):
                w.append(f"Rule {r.rule_id[1:]}, step {s.step}: ignored the deadline {s.deadline.value:g}, because it is not in the contract")
                s.deadline = None
    return w


def merge_fallback_rules(ir: ClauseIR) -> list[str]:
    """A 'rule' whose trigger is a fallback phrase ("if neither of these could be made") continues the previous
    rule: its steps are appended to that chain. Uses the S2 fallback patterns."""
    import re
    from .lexicon import FALLBACK_RULES
    w, kept = [], []
    for r in ir.rules:
        trig = " ".join(c.text for c in r.trigger.conditions)
        cands = [trig.strip() + ",", "If " + trig.strip() + ","]  # LLM trigger texts drop the opener and the comma
        is_fb = any(re.search(p, c, flags=re.IGNORECASE) for p in FALLBACK_RULES for c in cands)
        if kept and is_fb:
            prev = kept[-1]
            for s in r.chain:
                s.step = len(prev.chain) + 1
                prev.chain.append(s)
            w.append(f"Joined a separate rule into rule {prev.rule_id[1:]}, because it starts with a fallback phrase")
        else:
            kept.append(r)
    for k, r in enumerate(kept, start=1):
        r.rule_id = f"R{k}"
    ir.rules = kept
    return w
