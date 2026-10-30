"""S5 Compile: IR -> BPMN 2.0 collaboration XML with diagram layout (DI) for bpmn.io.

Mapping (plan §4.4):
- party -> participant (pool). Parties that act get a process; parties that only receive get a collapsed pool.
- rule trigger -> start event in the pool of the first actor, labeled with the conditions.
- O step -> task in the actor pool + message flow to each beneficiary pool.
- P step -> task + text annotation "permitted"; F step -> text annotation.
- between steps (failing which) -> exclusive gateway "fulfilled?": yes -> end "Compensated", no -> next step.
- last step fails -> end event "Uncompensated".
- deadline -> timer boundary event on the task, leading to the "no" path.
- next step by another party -> message end event in this pool, message start event in the next pool
  (sequence flows may not cross pools in BPMN).
Labels follow one fixed pattern; numbers (amounts, thresholds, deadlines) are always written in labels.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from .ir import ClauseIR, Deadline, Step

NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
}
SIZE = {"task": (170, 90), "event": (36, 36), "gateway": (50, 50), "annotation": (150, 40)}
COL_W, ROW_H, POOL_X, POOL_LABEL_W, POOL_PAD = 200, 130, 60, 30, 25
NARROW_W = 120  # columns that hold only an event or a gateway
COLLAPSED_H = 60

UNIT_WORD = {"minute": "minutes", "hour": "hours", "day": "days", "business_day": "business days"}


def fmt_num(v) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v)


def fmt_amount(amount, unit) -> str:
    if amount is None:
        return ""
    if unit == "percent":
        return f"{fmt_num(amount)}%"
    if unit == "USD":
        return f"${fmt_num(amount)}"
    return f"{fmt_num(amount)} {unit or ''}".strip()


def fmt_deadline(d: Deadline | None) -> str:
    if d is None:
        return ""
    if d.value is not None and d.unit:
        s = f"within {fmt_num(d.value)} {UNIT_WORD.get(d.unit, d.unit)}"
        return s + (f" of {d.anchor}" if d.anchor else "")
    return d.anchor or ""


def step_label(s: Step) -> str:
    parts = []
    for a in s.actions:
        amt = fmt_amount(a.amount, a.unit)
        parts.append(f"{a.label} ({amt})" if amt and amt not in a.label else a.label)
    return "; ".join(parts)


def cond_label(c) -> str:
    if c.op in (">", ">=", "<", "<=") and c.value is not None:
        return f"{c.text} ({c.op} {fmt_num(c.value)} {UNIT_WORD.get(c.unit, c.unit or '')})".replace(" )", ")")
    return c.text


@dataclass
class Node:
    id: str
    kind: str          # startEvent, endEvent, task, exclusiveGateway, boundaryEvent, textAnnotation
    pool: str
    col: float
    row: int
    name: str = ""
    extra: str = ""    # inner XML (event definitions, documentation)
    attrs: dict = field(default_factory=dict)


class _Builder:
    def __init__(self, ir: ClauseIR):
        self.ir = ir
        self.nodes: list[Node] = []
        self.flows: list[tuple[str, str, str, str]] = []      # (id, src, tgt, name) sequence flows
        self.msgs: list[tuple[str, str, str, str]] = []       # (id, src node or participant, tgt, name)
        self.assoc: list[tuple[str, str, str]] = []           # (id, src, tgt)
        self.ids = itertools.count(1)
        self.col = 0.0

    def nid(self, prefix):
        return f"{prefix}_{next(self.ids)}"

    def add(self, kind, pool, col, row, name="", extra="", **attrs) -> Node:
        n = Node(self.nid(kind[0].upper() + kind[1:6]), kind, pool, col, row, name, extra, attrs)
        self.nodes.append(n)
        return n

    def seq(self, a: Node, b: Node, name=""):
        self.flows.append((self.nid("Flow"), a.id, b.id, name))

    def build(self):
        for rule in self.ir.rules:
            self._rule(rule)
            self.col += 1.0
        return self

    def _rule(self, rule):
        first = rule.chain[0]
        mode = " or " if rule.trigger.mode == "any" else " and "
        start = self.add("startEvent", first.actor, self.col, 0,
                         name=mode.join(cond_label(c) for c in rule.trigger.conditions),
                         extra="<bpmn:conditionalEventDefinition><bpmn:condition xsi:type=\"bpmn:tFormalExpression\">"
                               + escape(mode.join(c.var for c in rule.trigger.conditions)) + "</bpmn:condition></bpmn:conditionalEventDefinition>")
        prev = start
        self.col += 1
        n = len(rule.chain)
        for k, s in enumerate(rule.chain):
            if k > 0 and s.actor != rule.chain[k - 1].actor:
                # hand over to another pool: message end event here, message start event there
                mend = self.add("endEvent", rule.chain[k - 1].actor, self.col, 0, name="request next remedy",
                                extra="<bpmn:messageEventDefinition/>")
                self.seq(prev, mend, "no" if prev.kind == "exclusiveGateway" else "")
                mstart = self.add("startEvent", s.actor, self.col, 0, name="remedy requested",
                                  extra="<bpmn:messageEventDefinition/>")
                self.msgs.append((self.nid("MessageFlow"), mend.id, mstart.id, "request"))
                for t in [n_ for n_ in self.nodes if n_.attrs.get("pending_fail")]:
                    self.seq(t, mend)
                    t.attrs.pop("pending_fail")
                prev = mstart
                self.col += 1
            task = self.add("task", s.actor, self.col, 0, name=step_label(s),
                            extra=f"<bpmn:documentation>{escape(s.modality)}({escape(self.ir.party(s.actor).name)}): "
                                  + escape(", ".join(a.remedy for a in s.actions)) + "</bpmn:documentation>")
            self.seq(prev, task, "no" if prev.kind == "exclusiveGateway" else "")
            for t in [n_ for n_ in self.nodes if n_.attrs.get("pending_fail")]:
                self.seq(t, task)
                t.attrs.pop("pending_fail")
            for b in s.beneficiaries:
                if b.party != s.actor:
                    share = "" if abs(b.share - 1.0) < 1e-9 else f" ({fmt_num(round(b.share * 100, 2))}% share)"
                    self.msgs.append((self.nid("MessageFlow"), task.id, f"Participant_{b.party}", "remedy" + share))
            if s.modality in ("P", "F"):
                ann = self.add("textAnnotation", s.actor, self.col, 1,
                               name="permitted (optional)" if s.modality == "P" else "prohibited")
                self.assoc.append((self.nid("Association"), task.id, ann.id))
            timer = None
            if s.deadline is not None and s.deadline.value is not None:
                timer = self.add("boundaryEvent", s.actor, self.col, 0, name=fmt_deadline(s.deadline) + " expired",
                                 extra="<bpmn:timerEventDefinition><bpmn:timeDuration xsi:type=\"bpmn:tFormalExpression\">"
                                       + iso_duration(s.deadline) + "</bpmn:timeDuration></bpmn:timerEventDefinition>",
                                 attachedTo=task.id)
            elif s.deadline is not None and s.deadline.anchor:
                task.name += f" [{s.deadline.anchor}]"
            self.col += 1
            last = k == n - 1
            if s.modality == "P" and last:
                end = self.add("endEvent", s.actor, self.col, 0, name="Compensated")
                self.seq(task, end)
                if timer:
                    timer.attrs["pending_fail"] = True
                    self._close_uncompensated(s.actor)
                self.col += 1
                return
            gw = self.add("exclusiveGateway", s.actor, self.col, 0, name=f"step {k + 1} fulfilled?")
            self.seq(task, gw)
            ok = self.add("endEvent", s.actor, self.col, 1, name="Compensated")
            self.seq(gw, ok, "yes")
            if timer:
                timer.attrs["pending_fail"] = True
            prev = gw
            self.col += 1
            if last:
                unc = self.add("endEvent", s.actor, self.col, 0, name="Uncompensated")
                self.seq(gw, unc, "no")
                for t in [n_ for n_ in self.nodes if n_.attrs.get("pending_fail")]:
                    self.seq(t, unc)
                    t.attrs.pop("pending_fail")
                self.col += 1

    def _close_uncompensated(self, pool):
        unc = self.add("endEvent", pool, self.col, 1, name="Uncompensated")
        for t in [n_ for n_ in self.nodes if n_.attrs.get("pending_fail")]:
            self.seq(t, unc)
            t.attrs.pop("pending_fail")


def iso_duration(d: Deadline) -> str:
    v = fmt_num(d.value)
    return {"minute": f"PT{v}M", "hour": f"PT{v}H", "day": f"P{v}D", "business_day": f"P{v}D"}[d.unit]


def compile_ir(ir: ClauseIR) -> str:
    b = _Builder(ir).build()
    acting = []
    for n in b.nodes:
        if n.pool not in acting:
            acting.append(n.pool)
    # receive-only pools (collapsed) sit right after the first acting pool, so most message flows are short
    passive = [p.id for p in ir.parties if p.id not in acting]
    order = acting[:1] + passive + acting[1:]
    # variable column widths: columns with a task are wide, columns with only events or gateways are narrow
    n_cols = int(max(1.0, b.col)) + 1
    task_cols = {int(n.col) for n in b.nodes if n.kind == "task"}
    col_w = [COL_W if c in task_cols else NARROW_W for c in range(n_cols)]
    col_x = [0.0]
    for w_ in col_w:
        col_x.append(col_x[-1] + w_)
    width = POOL_LABEL_W + int(col_x[int(max(1.0, b.col))]) + 2 * POOL_PAD

    # pool geometry
    pool_y, y = {}, 60
    for pid in order:
        h = 2 * ROW_H + 2 * POOL_PAD if pid in acting else COLLAPSED_H
        pool_y[pid] = (y, h)
        y += h + 40

    def center(n: Node):
        py, _ = pool_y[n.pool]
        c = int(n.col)
        cx = POOL_X + POOL_LABEL_W + POOL_PAD + col_x[c] + col_w[c] / 2
        cy = py + POOL_PAD + n.row * ROW_H + ROW_H / 2
        if n.kind == "boundaryEvent":  # bottom-right corner of its task
            cy += SIZE["task"][1] / 2
            cx += SIZE["task"][0] / 2 - 28
        return cx, cy

    def size(n: Node):
        return SIZE["task" if n.kind == "task" else "gateway" if n.kind == "exclusiveGateway"
                    else "annotation" if n.kind == "textAnnotation" else "event"]

    def bounds(n: Node):
        cx, cy = center(n)
        w, h = size(n)
        return cx - w / 2, cy - h / 2, w, h

    byid = {n.id: n for n in b.nodes}
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<bpmn:definitions ' + " ".join(f'xmlns:{k}="{v}"' for k, v in NS.items())
           + ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
           + f' id="Definitions_{escape(ir.clause_id)}" targetNamespace="http://termground.local/bpmn"'
           + ' exporter="TermGround" exporterVersion="0.1">']
    out.append(f'  <bpmn:collaboration id="Collaboration_{escape(ir.clause_id)}">')
    for pid in order:
        p = ir.party(pid)
        ref = f' processRef="Process_{pid}"' if pid in acting else ""
        out.append(f'    <bpmn:participant id="Participant_{pid}" name="{escape(p.name, {chr(34): "&quot;"})}"{ref} />')
    for mid, s, t, name in b.msgs:
        out.append(f'    <bpmn:messageFlow id="{mid}" name="{escape(name)}" sourceRef="{s}" targetRef="{t}" />')
    out.append("  </bpmn:collaboration>")

    q = {chr(34): "&quot;"}
    for pid in acting:
        out.append(f'  <bpmn:process id="Process_{pid}" isExecutable="false">')
        for n in [n for n in b.nodes if n.pool == pid]:
            inc = [f for f in b.flows if f[2] == n.id]
            outg = [f for f in b.flows if f[1] == n.id]
            if n.kind == "textAnnotation":
                continue  # artifacts must follow all flow elements (XSD order)
            attrs = f' id="{n.id}" name="{escape(n.name, q)}"'
            if n.kind == "boundaryEvent":
                attrs += f' attachedToRef="{n.attrs["attachedTo"]}" cancelActivity="true"'
            # documentation must come first in BPMN element content
            doc = ""
            extra = n.extra
            if extra.startswith("<bpmn:documentation>"):
                doc, extra = extra, ""
            body = doc + "".join(f"<bpmn:incoming>{f[0]}</bpmn:incoming>" for f in inc) \
                + "".join(f"<bpmn:outgoing>{f[0]}</bpmn:outgoing>" for f in outg) + extra
            out.append(f"    <bpmn:{n.kind}{attrs}>{body}</bpmn:{n.kind}>")
        for fid, s, t, name in b.flows:
            if byid[s].pool == pid:
                out.append(f'    <bpmn:sequenceFlow id="{fid}" name="{escape(name)}" sourceRef="{s}" targetRef="{t}" />')
        for n in [n for n in b.nodes if n.pool == pid and n.kind == "textAnnotation"]:
            out.append(f'    <bpmn:textAnnotation id="{n.id}"><bpmn:text>{escape(n.name)}</bpmn:text></bpmn:textAnnotation>')
        for aid, s, t in b.assoc:
            if byid[s].pool == pid:
                out.append(f'    <bpmn:association id="{aid}" sourceRef="{s}" targetRef="{t}" />')
        out.append("  </bpmn:process>")

    # ---- DI ----
    out.append('  <bpmndi:BPMNDiagram id="BPMNDiagram_1">')
    out.append(f'    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="Collaboration_{escape(ir.clause_id)}">')
    for pid in order:
        py, h = pool_y[pid]
        hz = ' isHorizontal="true"'
        out.append(f'      <bpmndi:BPMNShape id="Participant_{pid}_di" bpmnElement="Participant_{pid}"{hz}>'
                   f'<dc:Bounds x="{POOL_X}" y="{py}" width="{width}" height="{h}" /></bpmndi:BPMNShape>')
    def shape_label(n: Node, x, y_, w, h) -> str:
        """Explicit label positions, so bpmn.io does not draw labels over edges."""
        if n.kind == "exclusiveGateway":   # above the gateway
            return f'<bpmndi:BPMNLabel><dc:Bounds x="{x + w / 2 - 55:.0f}" y="{y_ - 32:.0f}" width="110" height="28" /></bpmndi:BPMNLabel>'
        if n.kind == "boundaryEvent":      # centered under the timer, below its outgoing line
            return f'<bpmndi:BPMNLabel><dc:Bounds x="{x + w / 2 - 50:.0f}" y="{y_ + h + 26:.0f}" width="100" height="40" /></bpmndi:BPMNLabel>'
        if n.kind == "endEvent" and "messageEventDefinition" in n.extra:  # hand-over: right of the event
            return f'<bpmndi:BPMNLabel><dc:Bounds x="{x + w + 4:.0f}" y="{y_ - 30:.0f}" width="75" height="28" /></bpmndi:BPMNLabel>'
        if n.kind in ("startEvent", "endEvent"):  # below the event
            return f'<bpmndi:BPMNLabel><dc:Bounds x="{x + w / 2 - 55:.0f}" y="{y_ + h + 4:.0f}" width="110" height="40" /></bpmndi:BPMNLabel>'
        return ""

    for n in b.nodes:
        x, y_, w, h = bounds(n)
        extra = ' isMarkerVisible="true"' if n.kind == "exclusiveGateway" else ""
        out.append(f'      <bpmndi:BPMNShape id="{n.id}_di" bpmnElement="{n.id}"{extra}>'
                   f'<dc:Bounds x="{x:.0f}" y="{y_:.0f}" width="{w}" height="{h}" />{shape_label(n, x, y_, w, h)}</bpmndi:BPMNShape>')

    def wp(pts):
        return "".join(f'<di:waypoint x="{x:.0f}" y="{y:.0f}" />' for x, y in pts)

    def port(n: Node, side: str):
        x, y_, w, h = bounds(n)
        return {"r": (x + w, y_ + h / 2), "l": (x, y_ + h / 2), "b": (x + w / 2, y_ + h), "t": (x + w / 2, y_),
                "bl": (x + w * 0.25, y_ + h), "tl": (x + w * 0.25, y_)}[side]

    def label(x, y, w=110, h=28):
        return f'<bpmndi:BPMNLabel><dc:Bounds x="{x:.0f}" y="{y:.0f}" width="{w}" height="{h}" /></bpmndi:BPMNLabel>'

    for fid, s, t, name in b.flows:
        a, c = byid[s], byid[t]
        if a.kind == "boundaryEvent":  # deadline expired: go below the gateway row, then to the next element
            p1 = port(a, "b")
            low = p1[1] + 22
            p2 = port(c, "b") if c.row == 0 else port(c, "t")
            pts = [p1, (p1[0], low), (p2[0], low), p2]
        elif a.row == c.row:
            pts = [port(a, "r"), port(c, "l")]
        elif c.row > a.row and abs(center(a)[0] - center(c)[0]) < 1:
            pts = [port(a, "b"), port(c, "t")]
        else:
            p1, p2 = port(a, "b"), port(c, "t") if c.row < a.row else port(c, "l")
            pts = [p1, (p1[0], p2[1]), p2] if c.row > a.row else [p1, p2]
        lab = ""
        if name == "no":
            lab = label((pts[0][0] + pts[1][0]) / 2 - 10, pts[0][1] - 22, 20, 14)
        elif name == "yes":
            lab = label(pts[0][0] + 6, (pts[0][1] + pts[-1][1]) / 2 - 7, 24, 14)
        out.append(f'      <bpmndi:BPMNEdge id="{fid}_di" bpmnElement="{fid}">{wp(pts)}{lab}</bpmndi:BPMNEdge>')
    for mid, s, t, _ in b.msgs:
        a = byid[s]
        src_side = ("bl", "tl") if a.kind == "task" else ("b", "t")
        if t in byid:
            c = byid[t]
            down = pool_y[c.pool][0] > pool_y[a.pool][0]
            p1 = port(a, src_side[0] if down else src_side[1])
            p2 = port(c, "t" if down else "b")
            mid_y = (pool_y[a.pool][0] + pool_y[a.pool][1] + pool_y[c.pool][0]) / 2 if down else \
                (pool_y[c.pool][0] + pool_y[c.pool][1] + pool_y[a.pool][0]) / 2
            pts = [p1, (p1[0], mid_y), (p2[0], mid_y), p2]
        else:
            tp = t.replace("Participant_", "")
            ty, th = pool_y[tp]
            down = ty > pool_y[a.pool][0]
            p1 = port(a, src_side[0] if down else src_side[1])
            pts = [p1, (p1[0], ty if down else ty + th)]
        lab = label(pts[0][0] + 6, pts[0][1] + (8 if pts[1][1] > pts[0][1] else -22), 90, 14)
        out.append(f'      <bpmndi:BPMNEdge id="{mid}_di" bpmnElement="{mid}">{wp(pts)}{lab}</bpmndi:BPMNEdge>')
    for aid, s, t in b.assoc:
        out.append(f'      <bpmndi:BPMNEdge id="{aid}_di" bpmnElement="{aid}">{wp([port(byid[s], "b"), port(byid[t], "t")])}</bpmndi:BPMNEdge>')
    out.append("    </bpmndi:BPMNPlane>")
    out.append("  </bpmndi:BPMNDiagram>")
    out.append("</bpmn:definitions>")
    return "\n".join(out) + "\n"
