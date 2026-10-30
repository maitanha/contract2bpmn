"""S1 Glossary: parties, defined terms, aliases and references.

Step 1 (rules): collect candidate party mentions
  - proper names (capitalized token sequences, names with hyphens or digits, bracketed names),
  - defined terms: Name (hereinafter "the X"), Name (the "X"), Name, referred to (below|hereinafter) as the X,
    the X, meaning Name / being Name,
  - generic role nouns preceded by "the" (the hotel, the company, the customer, ...).
Step 2 (LLM, constrained): group mentions into parties and give each party a type
  (Provider, Customer, Partner). The LLM may only use candidate strings (enum), so it cannot
  invent names. Without an LLM, a rule fallback groups defined terms with their names.
Step 3: rewrite the clause so every alias becomes the canonical party name.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

ROLE_NOUNS = [
    "provider", "service provider", "supplier", "vendor", "contractor", "seller", "lessor", "company", "firm",
    "operator", "carrier", "airline", "hotel", "insurer", "agency", "organizer", "organiser", "partner",
    "customer", "client", "buyer", "purchaser", "lessee", "renter", "tenant", "guest", "passenger", "tourist",
    "traveler", "traveller", "user", "consumer", "couple", "couples", "patron", "family", "member", "holder",
]
# generic sentence-start words and calendar words only (no words from test clauses, GUIDE §2.2)
STOP_CAPS = {"If", "In", "The", "This", "That", "These", "Those", "Should", "Failing", "Failure", "During", "Prior",
             "Upon", "When", "Where", "Unless", "Otherwise", "A", "An", "It", "Its", "Each", "All", "Any", "No",
             "Neither", "Both", "Before", "After", "Within", "For", "To", "On", "At", "By", "As", "And", "Or",
             "FALLBACK", "January", "February", "March", "April", "May", "June", "July", "August", "September",
             "October", "November", "December", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday"}

DEF_PATTERNS = [
    r"(?P<name>[A-Z][\w\-\.&' ]*?[\w\]])\s*\((?:hereinafter|hereafter)?\s*(?:referred to as\s*)?(?:the\s+)?[\"“']?(?:the\s+)?(?P<alias>[A-Z][\w\- ]*?)[\"”']?\)",
    r"(?P<name>[A-Z][\w\-\.&' ]*?[\w\]]),\s*(?:referred to|hereinafter referred to|called)\s+(?:below|hereinafter|herein)?\s*as\s+(?:the\s+)?(?P<alias>[A-Z][\w\- ]*?),",
    r"\bthe\s+(?P<alias>[A-Z][\w\- ]*?),\s*(?:meaning|being|namely|i\.e\.,?)\s+(?P<name>[A-Z][\w\-\.&' ]*?[\w\]]),",
]


@dataclass
class Mention:
    text: str
    kind: str  # name | defined | role


@dataclass
class Glossary:
    parties: list[dict] = field(default_factory=list)   # {id, name, type, aliases}
    mentions: list[Mention] = field(default_factory=list)
    definitions: list[tuple[str, str]] = field(default_factory=list)  # (name, alias)

    def alias_map(self) -> dict[str, str]:
        m = {}
        for p in self.parties:
            for a in p["aliases"]:
                m[a] = p["name"]
        return m


def candidate_mentions(text: str) -> tuple[list[Mention], list[tuple[str, str]]]:
    found: dict[str, Mention] = {}
    defs = []
    for pat in DEF_PATTERNS:
        for m in re.finditer(pat, text):
            name, alias = m.group("name").strip(), m.group("alias").strip()
            name = re.sub(r"^(?:The|the)\s+", "", name)
            defs.append((name, alias))
            found.setdefault(name, Mention(name, "name"))
            found.setdefault("the " + alias, Mention("the " + alias, "defined"))
            found.setdefault(alias, Mention(alias, "defined"))
    # bracketed names, e.g. [Trusted Insurer]
    for m in re.finditer(r"\[[^\]]{2,40}\]", text):
        found.setdefault(m.group(0), Mention(m.group(0), "name"))
    # proper names: capitalized tokens, allowing hyphen/digit tokens (Supplier-B, Unit 2, ACME Parts)
    tok = r"(?:[A-Z][A-Za-z0-9]*(?:-[A-Z0-9][A-Za-z0-9]*)*)"
    for m in re.finditer(rf"\b{tok}(?:\s+(?:{tok}|\d+))*", text):
        s = m.group(0)
        words = s.split()
        while words and words[0] in STOP_CAPS:
            words = words[1:]
        while words and words[-1] in STOP_CAPS:
            words = words[:-1]
        s = " ".join(words)
        if not s or s in STOP_CAPS or len(s) < 2:
            continue
        found.setdefault(s, Mention(s, "name"))
    # role nouns
    for r in ROLE_NOUNS:
        for m in re.finditer(rf"\bthe\s+(?:\w+\s+)?{r}\b", text, flags=re.IGNORECASE):
            s = m.group(0)
            if s.lower().split()[1] in ("same", "first", "second", "original", "initial", "scheduled"):
                continue
            found.setdefault(s, Mention(s, "role"))
    return list(found.values()), defs


def rule_glossary(text: str) -> Glossary:
    """Fallback without an LLM: one party per proper name; defined terms join their name."""
    mentions, defs = candidate_mentions(text)
    g = Glossary(mentions=mentions, definitions=defs)
    alias_of = {}
    for name, alias in defs:
        alias_of[alias] = name
        alias_of["the " + alias] = name
    k = 0
    for m in mentions:
        if m.kind == "name" and m.text not in alias_of:
            k += 1
            g.parties.append({"id": f"P{k}", "name": m.text, "type": "Provider" if k == 1 else "Customer",
                              "aliases": [a for a, n in alias_of.items() if n == m.text]})
    return g


def glossary_schema(mentions: list[Mention]) -> dict:
    """JSON schema for the constrained LLM call: names and aliases must be candidate strings."""
    cands = sorted({m.text for m in mentions})
    return {
        "type": "object",
        "properties": {"parties": {"type": "array", "minItems": 1, "maxItems": 6, "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "enum": cands},
                "type": {"type": "string", "enum": ["Provider", "Customer", "Partner"]},
                "aliases": {"type": "array", "maxItems": 6, "items": {"type": "string", "enum": cands}},
            },
            "required": ["name", "type", "aliases"]}}},
        "required": ["parties"],
    }


GLOSSARY_PROMPT = """You read a contract clause and list its parties.
A party is an organization or person that gives or receives a remedy (a provider, its customer, or a partner such as an insurer).
Group every mention that refers to the same party: use the most specific name as "name" and put the other mentions in "aliases".
Defined terms in the text have already been found: {defs}
Type: Provider = gives the service; Customer = receives the service and the compensation; Partner = third party that helps the provider.
Only use these candidate mentions: {cands}

Clause:
{text}

Answer in JSON."""


def build_prompt(text: str, mentions: list[Mention], defs) -> str:
    d = "; ".join(f'"{a}" means "{n}"' for n, a in defs) or "none"
    return GLOSSARY_PROMPT.format(defs=d, cands=json.dumps(sorted({m.text for m in mentions})), text=text)


def from_llm(obj: dict, mentions, defs) -> Glossary:
    g = Glossary(mentions=mentions, definitions=defs)
    used = set()
    for p in obj.get("parties", []):
        name = p["name"]
        if name in used:
            continue
        used.add(name)
        aliases = [a for a in p.get("aliases", []) if a != name and a not in used]
        used.update(aliases)
        # defined terms always follow their definition
        for n, a in defs:
            if n == name:
                for x in (a, "the " + a):
                    if x not in aliases:
                        aliases.append(x)
        g.parties.append({"id": f"P{len(g.parties) + 1}", "name": name, "type": p["type"], "aliases": aliases})
    return g


def apply(text: str, g: Glossary) -> str:
    """Rewrite aliases to canonical names; drop defined-term introductions."""
    t = text
    for name, alias in g.definitions:
        t = re.sub(rf"\s*\((?:hereinafter|hereafter)?\s*(?:referred to as\s*)?(?:the\s+)?[\"“']?(?:the\s+)?{re.escape(alias)}[\"”']?\)", "", t)
        t = re.sub(rf",\s*(?:referred to|hereinafter referred to|called)\s+(?:below|hereinafter|herein)?\s*as\s+(?:the\s+)?{re.escape(alias)},", ",", t)
        t = re.sub(rf"\bthe\s+{re.escape(alias)},\s*(?:meaning|being|namely|i\.e\.,?)\s+({re.escape(name)}),", r"\1,", t)
    pairs = sorted(((a, p["name"]) for p in g.parties for a in p["aliases"]), key=lambda x: -len(x[0]))
    for a, name in pairs:
        if a and a != name:
            t = re.sub(rf"(?<![\w-]){re.escape(a)}(?![\w-])", name, t)
    return t


# ---------------------------------------------------------------- hybrid glossary (rules first, LLM for the rest)
# Head words of generic role nouns and the party type they point to (general service-contract vocabulary).
ROLE_TYPE = {
    **{w: "Customer" for w in ["customer", "client", "buyer", "purchaser", "lessee", "renter", "tenant", "guest",
                               "passenger", "tourist", "traveler", "traveller", "user", "consumer", "couple",
                               "couples", "patron", "family", "member", "holder"]},
    **{w: "Provider" for w in ["provider", "supplier", "vendor", "contractor", "seller", "lessor", "company", "firm",
                               "operator", "carrier", "airline", "hotel", "agency", "organizer", "organiser"]},
    **{w: "Partner" for w in ["insurer", "partner"]},
}


def role_head(mention: str) -> str | None:
    words = re.findall(r"[a-z]+", mention.lower())
    for w in reversed(words):
        if w in ROLE_TYPE:
            return w
    return None


def name_type_schema(names: list[str]) -> dict:
    return {"type": "object", "properties": {"names": {"type": "array", "minItems": 1, "maxItems": max(1, len(names)),
            "items": {"type": "object", "properties": {
                "name": {"type": "string", "enum": names},
                "type": {"type": "string", "enum": ["Provider", "Customer", "Partner", "NotAParty"]}},
                "required": ["name", "type"]}}}, "required": ["names"]}


NAME_TYPE_PROMPT = """Contract clause:
{text}

For each name below, say whether it is a party of the contract and which kind:
Provider = gives the service and the remedies; Customer = receives the service and the compensation;
Partner = third party that helps the provider (for example an insurer); NotAParty = a place, product, test, holiday or anything that is not a party.
Names: {names}
Answer in JSON."""


def hybrid_glossary(text: str, typer=None) -> Glossary:
    """Proper names and defined terms become parties; generic role nouns join a party by their head word.

    `typer(text, names) -> {name: type}` is an optional constrained LLM call that types proper names.
    Without it, the first name is the Provider and the others are Customers.
    """
    mentions, defs = candidate_mentions(text)
    g = Glossary(mentions=mentions, definitions=defs)
    alias_of = {}
    for name, alias in defs:
        alias_of[alias] = name
        alias_of["the " + alias] = name
    names = [m.text for m in mentions if m.kind == "name" and m.text not in alias_of]
    # drop names that are contained in a longer name (e.g. "Lotus" inside "Lotus Inn")
    names = [n for n in names if not any(n != o and n in o for o in names)]
    types = typer(text, names) if (typer and names) else {}
    modal = r"\s+(?:will|shall|must|may|is obliged|is required|undertakes|agrees|has to|is entitled)\b"
    for k, n in enumerate(names):
        t = types.get(n) or ("Provider" if k == 0 else "Customer")
        defined = any(nn == n for _, nn in alias_of.items())
        acts = re.search(re.escape(n) + modal, text) or any(re.search(re.escape(a_) + modal, text)
                                                            for a_, nn in alias_of.items() if nn == n)
        if t == "NotAParty" and (defined or acts):
            t = "Provider" if not any(p["type"] == "Provider" for p in g.parties) else "Customer"
        if t == "NotAParty":
            continue
        g.parties.append({"id": f"P{len(g.parties) + 1}", "name": n, "type": t,
                          "aliases": [a for a, nn in alias_of.items() if nn == n]})
    # generic role nouns: alias of the only party of that type, or a new party when none exists
    roles = [m.text for m in mentions if m.kind == "role"]
    for r in sorted(roles, key=len, reverse=True):
        head = role_head(r)
        if head is None:
            continue
        rtype = ROLE_TYPE[head]
        same = [p for p in g.parties if p["type"] == rtype]
        if len(same) == 1:
            if r not in same[0]["aliases"] and r != same[0]["name"]:
                same[0]["aliases"].append(r)
        elif not same:
            g.parties.append({"id": f"P{len(g.parties) + 1}", "name": r[4:].strip().title() if r.lower().startswith("the ") else r.title(),
                              "type": rtype, "aliases": [r]})
        # several parties of the same type: leave the role noun unresolved (the extraction prompt sees all names)
    # a shorter role noun ("the tourist") joins a party created from a longer one ("the tourist family")
    for p in g.parties:
        for r in roles:
            if role_head(r) and any(a.endswith(role_head(r)) or role_head(r) in a for a in p["aliases"]) \
                    and r not in p["aliases"] and ROLE_TYPE[role_head(r)] == p["type"] \
                    and len([q for q in g.parties if q["type"] == p["type"]]) == 1:
                p["aliases"].append(r)
    if not g.parties:
        return rule_glossary(text)
    return g
