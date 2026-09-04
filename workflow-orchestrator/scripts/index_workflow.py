#!/usr/bin/env python3
"""Generate the global structured task index.

Scans every project directory under the workflow data root and writes one
machine-readable manifest to <workflow-dir>/tasks.json. The HTML renderer
embeds this same global shape, so task history is not split into per-project
reports.

Usage:
  python3 index_workflow.py
  python3 index_workflow.py <project-dir>  # explicit single-project scan
"""

import json
import os
import sys
import time

import _workflow_paths
from _workflow_steps import derive_step_progress, is_status_only, is_standalone_script

TERMINALS = ("done", "escalated")


def _parse_date(s):
    """Parse 'YYYY-MM-DD' -> (year, month, day) tuple, or None."""
    if not s:
        return None
    try:
        y, m, d = s.strip().split("-")
        return (int(y), int(m), int(d))
    except (ValueError, AttributeError):
        return None


def _local_date(ts):
    """Unix seconds -> local (year, month, day) tuple, or None."""
    if not isinstance(ts, (int, float)):
        return None
    lt = time.localtime(ts)
    return (lt.tm_year, lt.tm_mon, lt.tm_mday)


def in_date_range(task, d_from, d_to):
    """True when the task's start date falls within [d_from, d_to] (inclusive).

    ``d_from`` / ``d_to`` are (year, month, day) tuples; ``None`` means
    unbounded on that side. A task with no derivable start date is excluded
    whenever any bound is set (it cannot be date-filtered).
    """
    started = task.get("started_at")
    d = _local_date(started)
    if d is None:
        return False
    if d_from is not None and d < d_from:
        return False
    if d_to is not None and d > d_to:
        return False
    return True


def summarize(path, project):
    try:
        with open(path, "r", encoding="utf-8") as f:
            ledger = json.load(f)
    except Exception as e:
        sys.stderr.write('WARN: skipping unreadable "%s": %s\n' % (path, e))
        return None
    events = ledger.get("events") if isinstance(ledger.get("events"), list) else []
    state = events[-1].get("to") if events else None
    wi = ledger.get("work_item") or {}

    def first_t(pred):
        for ev in events:
            if pred(ev) and isinstance(ev.get("t"), (int, float)):
                return ev["t"]
        return None

    started = wi.get("started_at")
    if started is None:
        started = first_t(lambda ev: ev.get("from") is None)
    completed = wi.get("completed_at")
    if completed is None:
        completed = first_t(lambda ev: ev.get("to") == "done")

    progress = derive_step_progress(events, wi)
    routing = wi.get("routing")
    change_class = wi.get("change_class")
    status_only = is_status_only(events, wi)
    standalone_script = is_standalone_script(events, wi)
    routing_summary = None
    if isinstance(routing, dict):
        routing_summary = {
            "scenario": routing.get("scenario"),
            "phase": routing.get("phase"),
            "tier": routing.get("tier"),
            "used": routing.get("used") or [],
        }

    return {
        "project": project,
        "id": wi.get("id") or os.path.splitext(os.path.basename(path))[0],
        "file": os.path.basename(path),
        "title": wi.get("title"),
        "kind": wi.get("kind"),
        "state": state,
        "terminal": state in TERMINALS,
        "current_step": progress["current_step"],
        "steps": progress["steps"],
        "events": events,
        "event_count": len(events),
        "started_at": started,
        "completed_at": completed,
        "changed_files": wi.get("changed_files") or [],
        "change_class": change_class or ("standalone-script" if standalone_script else ("status-only" if status_only else ("code" if any(ev.get("has_code_change") is True for ev in events) else "none"))),
        "status_only": status_only,
        "status_only_reason": (wi.get("status_only") or {}).get("reason") if isinstance(wi.get("status_only"), dict) else None,
        "standalone_script": wi.get("standalone_script") if standalone_script else None,
        "routing": routing_summary,
        "qa_report": wi.get("qa_report") or ledger.get("qa_report"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(path))),
    }


def scan_project(d):
    project = os.path.basename(os.path.normpath(d))
    tasks = []
    for fn in _workflow_paths.ledger_files(d):
        row = summarize(os.path.join(d, fn), project)
        if row is not None:
            tasks.append(row)
    return sorted(tasks, key=lambda t: (t["id"].lower(), t["file"]))


def build_index(project_paths=None, date_from=None, date_to=None):
    d_from = _parse_date(date_from)
    d_to = _parse_date(date_to)
    paths = project_paths if project_paths is not None else _workflow_paths.project_dirs()
    projects = []
    tasks = []
    for d in paths:
        project_tasks = scan_project(d)
        if d_from is not None or d_to is not None:
            project_tasks = [t for t in project_tasks if in_date_range(t, d_from, d_to)]
        if not project_tasks:
            continue
        name = os.path.basename(os.path.normpath(d))
        projects.append({"name": name, "task_count": len(project_tasks), "tasks": project_tasks})
        tasks.extend(project_tasks)
    projects.sort(key=lambda p: p["name"].lower())
    tasks.sort(key=lambda t: (t["project"].lower(), t["id"].lower(), t["file"]))
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "project_count": len(projects),
        "task_count": len(tasks),
        "projects": projects,
        "tasks": tasks,
    }


def main(argv):
    if argv:
        project_paths = [os.path.abspath(argv[0])]
    else:
        project_paths = None
    index = build_index(project_paths)
    root = _workflow_paths.workflow_dir()
    os.makedirs(root, exist_ok=True)
    out_path = os.path.join(root, "tasks.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("Wrote %s (%d projects, %d tasks)" % (out_path, index["project_count"], index["task_count"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
