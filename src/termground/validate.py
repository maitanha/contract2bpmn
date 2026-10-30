"""BPMN checks used by S5 and by the Valid metric (plan §7).

valid = XML parses + passes the OMG BPMN 2.0 XSD + every flow node has a DI shape
        + every flow has a DI edge + (optional) bpmn-moddle import without warnings (node).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from lxml import etree

SCHEMA_DIR = Path(__file__).parent / "schema"
MODDLE_DIR = Path(__file__).resolve().parents[2] / "tools" / "moddle"
B = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
DI = "{http://www.omg.org/spec/BPMN/20100524/DI}"
SHAPED = {"task", "userTask", "serviceTask", "sendTask", "receiveTask", "manualTask", "scriptTask",
          "businessRuleTask", "subProcess", "callActivity", "startEvent", "endEvent",
          "intermediateThrowEvent", "intermediateCatchEvent", "boundaryEvent", "exclusiveGateway",
          "parallelGateway", "inclusiveGateway", "eventBasedGateway", "complexGateway", "participant",
          "textAnnotation", "dataObjectReference", "dataStoreReference"}
EDGED = {"sequenceFlow", "messageFlow", "association"}


@lru_cache(maxsize=1)
def _schema():
    return etree.XMLSchema(etree.parse(str(SCHEMA_DIR / "BPMN20.xsd")))


def check(xml: str, moddle: bool = True) -> dict:
    res = {"parse": False, "xsd": False, "di": False, "moddle": None, "errors": []}
    try:
        doc = etree.fromstring(xml.encode() if isinstance(xml, str) else xml)
        res["parse"] = True
    except Exception as e:  # noqa: BLE001
        res["errors"].append(f"parse: {e}")
        return res
    sch = _schema()
    res["xsd"] = sch.validate(doc)
    if not res["xsd"]:
        res["errors"] += [f"xsd: {e.message}" for e in list(sch.error_log)[:5]]
    elems = {el.get("id"): etree.QName(el).localname for el in doc.iter() if el.get("id")
             and etree.QName(el).namespace == B[1:-1]}
    di_refs = {el.get("bpmnElement") for el in doc.iter(DI + "BPMNShape", DI + "BPMNEdge")}
    missing = []
    for eid, kind in elems.items():
        if kind in SHAPED or kind in EDGED:
            # collapsed participants and boundary events also need shapes
            if eid not in di_refs:
                missing.append(eid)
    has_plane = doc.find(f".//{DI}BPMNPlane") is not None
    res["di"] = has_plane and not missing
    if missing:
        res["errors"].append(f"di: {len(missing)} elements without DI, e.g. {missing[:3]}")
    if not has_plane:
        res["errors"].append("di: no BPMNDiagram/BPMNPlane (bpmn.io shows 'no diagram to display')")
    if moddle and moddle_available():
        res["moddle"] = _moddle(xml)
        if not res["moddle"]["ok"]:
            res["errors"] += [f"moddle: {w}" for w in res["moddle"]["warnings"][:5]]
    res["valid"] = res["parse"] and res["xsd"] and res["di"] and (res["moddle"] is None or res["moddle"]["ok"])
    return res


def moddle_available() -> bool:
    return shutil.which("node") is not None and (MODDLE_DIR / "check.mjs").exists()


def _moddle(xml: str) -> dict:
    p = subprocess.run(["node", str(MODDLE_DIR / "check.mjs")], input=xml, capture_output=True, text=True,
                       cwd=MODDLE_DIR, timeout=60)
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "warnings": [p.stderr.strip()[:300] or "no output"]}
