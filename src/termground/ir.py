"""Intermediate representation (IR) of a penalty clause (plan §4.1).

A clause has parties and one or more rules. A rule has a trigger and an ordered chain of
remedy steps: step i+1 runs only if step i fails ("failing which", the reparation operator).
The last step is the last chance to compensate; if it fails the rule ends uncompensated.

Conventions (see data/gold/REVIEW.md):
- "X is entitled to R" is an obligation (O) of the party that must give R.
- "X may do R" where X takes the remedy itself (e.g. a payment deduction) is a permission (P) of X.
- A primary duty (e.g. "the hotel will provide a sea-view room") is not a step; its breach is the trigger.
- Severity levels ("the compensation escalates to ...") are separate rules with their own triggers.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator

PartyType = Literal["Provider", "Customer", "Partner"]
Modality = Literal["O", "P", "F"]
Op = Literal["=", "!=", ">", ">=", "<", "<="]
TimeUnit = Literal["minute", "hour", "day", "business_day"]

# Remedy taxonomy (plan §4.2). Parent links give the is-a hierarchy used by S2.
REMEDY_PARENT = {
    "Repair": "PerformanceRemedy",
    "Replacement": "PerformanceRemedy",
    "SubstitutePerformance": "PerformanceRemedy",
    "AdditionalService": "PerformanceRemedy",
    "PriceReduction": "MonetaryRemedy",
    "Refund": "MonetaryRemedy",
    "Damages": "MonetaryRemedy",
    "ServiceCredit": "MonetaryRemedy",
    "Termination": "Remedy",
    "Other": "Remedy",
}
RemedyType = Literal["Repair", "Replacement", "SubstitutePerformance", "AdditionalService",
                     "PriceReduction", "Refund", "Damages", "ServiceCredit", "Termination", "Other"]


class Party(BaseModel):
    id: str
    name: str
    type: PartyType
    aliases: list[str] = Field(default_factory=list)


class Condition(BaseModel):
    text: str
    var: str
    op: Op = "="
    value: Union[bool, float, str, None] = None
    unit: Optional[str] = None


class Trigger(BaseModel):
    mode: Literal["all", "any"] = "all"
    conditions: list[Condition] = Field(min_length=1)


class Beneficiary(BaseModel):
    party: str
    share: float = 1.0


class Action(BaseModel):
    remedy: RemedyType
    label: str
    amount: Optional[float] = None
    unit: Optional[str] = None  # "percent", "USD", ...


class Deadline(BaseModel):
    value: Optional[float] = None
    unit: Optional[TimeUnit] = None
    anchor: Optional[str] = None


class Step(BaseModel):
    step: int
    modality: Modality
    actor: str
    beneficiaries: list[Beneficiary] = Field(default_factory=list)
    actions: list[Action] = Field(min_length=1)
    deadline: Optional[Deadline] = None


class Rule(BaseModel):
    rule_id: str
    trigger: Trigger
    chain: list[Step] = Field(min_length=1)
    on_exhausted: Literal["Uncompensated"] = "Uncompensated"


class ClauseIR(BaseModel):
    clause_id: str
    status: Literal["draft", "checked", "predicted"] = "predicted"
    notes: str = ""
    parties: list[Party] = Field(min_length=1)
    rules: list[Rule] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_refs(self):
        ids = {p.id for p in self.parties}
        if len(ids) != len(self.parties):
            raise ValueError("duplicate party id")
        for r in self.rules:
            for k, s in enumerate(r.chain, start=1):
                if s.step != k:
                    raise ValueError(f"{r.rule_id}: steps must be numbered 1..n in order")
                if s.actor not in ids:
                    raise ValueError(f"{r.rule_id} step {k}: unknown actor {s.actor}")
                for b in s.beneficiaries:
                    if b.party not in ids:
                        raise ValueError(f"{r.rule_id} step {k}: unknown beneficiary {b.party}")
                if s.beneficiaries and not math.isclose(sum(b.share for b in s.beneficiaries), 1.0, abs_tol=1e-6):
                    raise ValueError(f"{r.rule_id} step {k}: beneficiary shares must sum to 1")
        return self

    # convenience
    def party(self, pid: str) -> Party:
        return next(p for p in self.parties if p.id == pid)

    @classmethod
    def load(cls, path: str | Path) -> "ClauseIR":
        return cls.model_validate(json.loads(Path(path).read_text()))

    def dump(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2, exclude_none=False) + "\n")


def remedy_ancestors(remedy: str) -> list[str]:
    """is-a chain, e.g. Refund -> MonetaryRemedy -> Remedy."""
    out = [remedy]
    p = REMEDY_PARENT.get(remedy)
    while p:
        out.append(p)
        p = {"PerformanceRemedy": "Remedy", "MonetaryRemedy": "Remedy"}.get(p)
    return out
