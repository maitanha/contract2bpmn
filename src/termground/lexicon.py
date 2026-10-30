"""S2 Lexicon: canonical deontic and connective forms, and remedy-word hints.

Built from general legal drafting usage and the deontic categories of the literature
(obligation / permission / prohibition: Chalkidis et al. 2018; Sancheti et al. 2022;
LegalRuleML), plus the dev clause L1. It was written without reading the variant phrase
file (data/variant_phrases.json) and without tuning on the test clauses (GUIDE §2.2).

normalize(text) returns the text with
  - modal expressions rewritten to one canonical form per modality
      obligation -> "shall", permission -> "may", prohibition -> "shall not",
      entitlement of the beneficiary -> "is entitled to";
  - fallback connectives ("failing which" and conditional phrases that refer back to a
    previous remedy) marked with the canonical cue "[FALLBACK]";
and a list of remedy hints (word -> remedy type) for the extraction prompt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

BE = r"(?:is|are|was|were|be|shall be|will be)"

# order matters: longer and negative forms first
MODAL_RULES: list[tuple[str, str, str]] = [
    # prohibition
    ("F", rf"\b(?:shall|must|will|may)\s+not\b", "shall not"),
    ("F", r"\b(?:shan't|mustn't|won't)\b", "shall not"),
    ("F", rf"\b{BE}\s+(?:prohibited|forbidden|barred|not permitted|not allowed)\s+(?:from|to)\b", "shall not"),
    # entitlement (obligation of the other party)
    ("E", rf"\b{BE}\s+entitled\s+to\b", "is entitled to"),
    ("E", r"\b(?:has|have|shall have|will have)\s+(?:a|the)\s+right\s+to\s+(?=(?:a|an|the|full|free|\d))", "is entitled to "),
    # permission
    ("P", rf"\b{BE}\s+(?:permitted|allowed|authori[sz]ed)\s+to\b", "may"),
    ("P", r"\b(?:has|have|shall have|will have)\s+(?:a|the)\s+right\s+to\b", "may"),
    ("P", r"\b(?:is|are)\s+free\s+to\b", "may"),
    # obligation
    ("O", rf"\b{BE}\s+(?:required|obliged|obligated|bound|expected|responsible)\s+(?:to|for)\b", "shall"),
    ("O", r"\b(?:has|have)\s+to\b", "shall"),
    ("O", r"\b(?:agrees|agree|undertakes|undertake|commits|commit|covenants|covenant)\s+to\b", "shall"),
    ("O", r"\bmust\b", "shall"),
    # in contract drafting "will" states a commitment (generic rule; no verb list from the clauses)
    ("O", r"\bwill\b", "shall"),
]

# words that state that something failed or is not available
_FAIL = (r"(?:not|no|never|neither|nor|unable|unavailable|unobtainable|cannot|can't|could not|fails?|failed|failure|"
         r"impossible|unfeasible|infeasible|impracticable|unsuccessful|out of reach|lacking|absent)")
# words that point back to an earlier remedy
_BACK = (r"(?:this|that|these|those|such|said|aforementioned|aforesaid|foregoing|above|preceding|previous|prior|"
         r"former|latter|neither|either|it|them|the same|so)")
# a conditional or failure opener at the start of a sentence or after ';'
_OPEN = r"(?:If|Should|Where|When|In case|In the event(?: that)?|Unless|Failing|Failure|In default of|Absent|Otherwise)"

FALLBACK_RULES: list[str] = [
    r"\bfailing\s+(?:which|that|this|these|such)\b",
    r"\b(?:or\s+)?else\b",
    rf"(?:(?<=^)|(?<=[.;:]\s)|(?<=,\s)){_OPEN}\b[^.;,]*?\b{_BACK}\b[^.;,]*?\b{_FAIL}\b[^.;,]*?(?=[,;]|\s+(?:results?|will result|shall result|leads?)\b)",
    rf"(?:(?<=^)|(?<=[.;:]\s)|(?<=,\s)){_OPEN}\b[^.;,]*?\b{_FAIL}\b[^.;,]*?\b{_BACK}\b[^.;,]*?(?=[,;]|\s+(?:results?|will result|shall result|leads?)\b)",
    r"(?:(?<=^)|(?<=[.;:]\s))Otherwise\b",
    r"(?:(?<=^)|(?<=[.;:]\s)|(?<=,\s))(?:Failure|Failing)\b[^.;,]*?(?=,|\s+(?:shall\s+|will\s+)?(?:results?|leads?)\b)",
]

# remedy hints: generic remedy vocabulary -> taxonomy type (plan §4.2)
REMEDY_HINTS: list[tuple[str, str]] = [
    (r"\brefund\w*|\breimburs\w*|\brepay\w*|\bmoney back\b|\breturn of the (?:price|fee)\b", "Refund"),
    (r"\bdiscount\w*|\bprice reduction\b|\breduction\b|\bdeduct\w*|\brebate\w*|\bwaiv\w*|\bat no (?:additional )?cost\b", "PriceReduction"),
    (r"\bcredit\b|\bvoucher\w*|\bcoupon\w*|\bservice credit\w*", "ServiceCredit"),
    (r"\bdamages\b|\bcompensation\b|\bcompensate\w*|\bindemn\w*|\bpenalt\w*|\b(?:failure|late) fee\b|\bliquidated\b", "Damages"),
    (r"\breplace\w*|\bsubstitute (?:item|part|component|product|equipment)\b|\bexchange\b", "Replacement"),
    (r"\brepair\w*|\bfix\w*|\brestor\w*|\bremedy the defect\w*|\bcorrect the\b", "Repair"),
    (r"\balternative\b|\bupgrade\w*|\bcomparable\b|\bequivalent\b|\bsubstitute\b|\binstead\b", "SubstitutePerformance"),
    (r"\bfree\b|\bcomplimentary\b|\bat no charge\b|\baccess to\b|\badditional service\b|\baccommodation\b", "AdditionalService"),
    (r"\bterminat\w*|\brescind\w*|\bcancel the (?:contract|agreement)\b", "Termination"),
]

FALLBACK_CUE = "[FALLBACK]"


@dataclass
class Normalized:
    text: str
    modal_hits: list[tuple[str, str]] = field(default_factory=list)   # (original phrase, modality)
    fallback_hits: list[str] = field(default_factory=list)
    remedy_hints: list[tuple[str, str]] = field(default_factory=list)  # (word, remedy type)


def normalize(text: str) -> Normalized:
    out = Normalized(text=text)
    t = text
    for mod, pat, canon in MODAL_RULES:
        def _sub(m, mod=mod, canon=canon):
            out.modal_hits.append((m.group(0), mod))
            return canon
        t = re.sub(pat, _sub, t, flags=re.IGNORECASE)
    for pat in FALLBACK_RULES:
        def _mark(m):
            out.fallback_hits.append(m.group(0))
            return f"{FALLBACK_CUE} {m.group(0)}"
        t = re.sub(rf"(?<!{re.escape(FALLBACK_CUE)} ){pat}", _mark, t, flags=re.IGNORECASE)
    seen = set()
    for pat, rtype in REMEDY_HINTS:
        for m in re.finditer(pat, t, flags=re.IGNORECASE):
            w = m.group(0).lower()
            if w not in seen:
                seen.add(w)
                out.remedy_hints.append((w, rtype))
    out.text = t
    return out
