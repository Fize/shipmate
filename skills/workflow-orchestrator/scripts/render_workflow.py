#!/usr/bin/env python3
"""Render workflow ledgers into a self-contained HTML report.

The default --all report is global: it scans every readable project directory
under ~/.local/share/dev-workflow/, writes the global tasks.json manifest, then
injects the aggregated data into templates/report.html. The generated HTML can
be opened directly from disk; it does not fetch JSON files at runtime.

Usage:
  python3 render_workflow.py <ledger.json> [--output report.html]
  python3 render_workflow.py --all [--output tasks.html]
"""

import json
import os
import sys

import _workflow_paths
from index_workflow import build_index, summarize

PLACEHOLDER = "__TASKS_JSON__"


def render_report(data):
    template_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "templates", "report.html"
    )
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()
    payload = json.dumps(data, ensure_ascii=False, indent=2).replace("</", "<\\/")
    if PLACEHOLDER not in template:
        sys.stderr.write('ERROR: placeholder "%s" not found in template\n' % PLACEHOLDER)
        return None
    return template.replace(PLACEHOLDER, payload)


def write_html(data, out_path):
    html_out = render_report(data)
    if html_out is None:
        return 1
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)
    print("Wrote %s (%d projects, %d tasks)" % (
        out_path, data.get("project_count", 1), data.get("task_count", len(data.get("tasks", [])))
    ))
    return 0


def write_global_index(data):
    root = _workflow_paths.workflow_dir()
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "tasks.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def render_all(output):
    data = build_index()
    write_global_index(data)
    root = _workflow_paths.workflow_dir()
    out_path = output or os.path.join(root, "tasks.html")
    return write_html(data, out_path)


def render_single(path, output):
    try:
        with open(path, "r", encoding="utf-8") as f:
            ledger = json.load(f)
    except Exception as e:
        sys.stderr.write('ERROR: cannot read/parse "%s": %s\n' % (path, e))
        return 1
    project = os.path.basename(os.path.dirname(os.path.abspath(path)))
    task = summarize(path, project)
    if task is None:
        return 1
    data = {
        "generated_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "project_count": 1,
        "task_count": 1,
        "projects": [{"name": project, "task_count": 1, "tasks": [task]}],
        "tasks": [task],
    }
    out_path = output or os.path.splitext(path)[0] + ".html"
    return write_html(data, out_path)


def main(argv):
    output = None
    all_mode = False
    file = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--all":
            all_mode = True
        elif arg == "--output":
            i += 1
            if i < len(argv):
                output = argv[i]
        elif not arg.startswith("--") and file is None:
            file = arg
        i += 1

    if all_mode:
        return render_all(output)
    if file is None:
        file = _workflow_paths.default_ledger_path()
        if not file:
            sys.stderr.write("Usage: python3 render_workflow.py <ledger.json> [--output report.html]\n")
            sys.stderr.write(_workflow_paths.project_ledger_hint() + "\n")
            return 1
    return render_single(file, output)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
