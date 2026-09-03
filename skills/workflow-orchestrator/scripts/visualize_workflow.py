#!/usr/bin/env python3
"""
visualize_workflow.py

Portable, zero-dependency renderer for a workflow ledger. Reads the same JSON shape as
validate_workflow.py (a single-layer, 5-state ledger: active/reviewing/needs_fix/done/escalated)
and emits a mermaid stateDiagram-v2 block and/or an ASCII compact flow.

Tool-agnostic: Python 3.9+ standard library only, no runtime dependency.

Ledger input schema: references/ledger-format.md; provide a ledger JSON path when visualizing.

Usage:
  python3 visualize_workflow.py <ledger.json> [--format mermaid|ascii|both]

--format  default "both"

Exit codes: 0 success, 1 unreadable/unknown-state input.
"""

import json
import sys

from _workflow_paths import default_ledger_path, project_ledger_hint

STATES = ["active", "reviewing", "needs_fix", "done", "escalated"]
TERMINALS = ["done", "escalated"]


def _safe_id(state):
    return "".join(c if c.isalnum() or c == "_" else "_" for c in state)


def build_chain(events):
    """Collapse the event list into a compact 'a -> b -> c' chain, annotating the
    reviewing state with its reviewer/lens roster and folding consecutive duplicates."""
    steps = []
    for ev in events:
        to = ev.get("to")
        if not to:
            continue
        if to == "reviewing" and isinstance(ev.get("reviewers"), list):
            steps.append("reviewing(%s)" % ",".join(ev["reviewers"]))
        elif to == "active" and ev.get("reason"):
            steps.append("active(%s)" % ev["reason"])
        else:
            steps.append(to)
    compact = []
    for s in steps:
        if compact and compact[-1]["base"] == s:
            compact[-1]["count"] += 1
        else:
            compact.append({"base": s, "count": 1})
    return " -> ".join("%s(x%d)" % (c["base"], c["count"]) if c["count"] > 1 else c["base"] for c in compact)


def render_mermaid(work_id, events):
    lines = ["%s Workflow: %s" % ("%%", work_id), "stateDiagram-v2"]
    emitted = set()
    for ev in events:
        to = ev.get("to")
        if not to:
            continue
        frm = ev.get("from")
        label = ev.get("reason") or ev.get("label") or ""
        if frm:
            src = _safe_id(frm)
            dst = _safe_id(to)
            edge_label = " : %s" % label if label else ""
            lines.append("  %s --> %s%s" % (src, dst, edge_label))
            emitted.add(src)
        emitted.add(_safe_id(to))
    for t in TERMINALS:
        key = _safe_id(t)
        if key in emitted:
            lines.append("  note right of %s : terminal" % key)
    return "\n".join(lines)


def render_ascii(work_id, events):
    return "workflow %s\n%s" % (work_id, build_chain(events))


def load_ledger(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main(argv):
    file = None
    fmt = "both"
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--format":
            i += 1
            if i < len(argv):
                fmt = argv[i]
        elif not a.startswith("--") and file is None:
            file = a
        i += 1

    if fmt not in ("mermaid", "ascii", "both"):
        sys.stderr.write('ERROR: unknown --format "%s" (expected mermaid|ascii|both)\n' % fmt)
        return 1
    if not file:
        file = default_ledger_path()
        if not file:
            sys.stderr.write("Usage: python3 visualize_workflow.py <ledger.json> [--format mermaid|ascii|both]\n")
            sys.stderr.write(project_ledger_hint() + "\n")
            return 1
        sys.stderr.write('using default ledger "%s"\n' % file)

    try:
        ledger = load_ledger(file)
    except Exception as e:
        sys.stderr.write('ERROR: cannot read/parse "%s": %s\n' % (file, e))
        return 1

    events = ledger.get("events") if isinstance(ledger.get("events"), list) else []
    work_item = ledger.get("work_item") or {}
    work_id = work_item.get("id") or "<unknown>"

    if fmt in ("mermaid", "both"):
        sys.stdout.write(render_mermaid(work_id, events) + "\n")
    if fmt in ("ascii", "both"):
        sys.stdout.write(render_ascii(work_id, events) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
