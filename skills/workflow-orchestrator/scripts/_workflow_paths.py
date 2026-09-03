"""Shared paths for workflow ledgers and the global report.

Data layout (system-level, no project hash):

    ${XDG_DATA_HOME:-$HOME/.local/share}/dev-workflow/
    ├── tasks.json                 global structured task index
    ├── tasks.html                 global aggregated report
    ├── <project-name>/            one directory per project
    │   └── <work-item-id>.json    one ledger per task
    └── ...

Project names are the basename of the nearest git root (or current directory
for non-git projects). This intentionally favors a readable history directory;
if two projects have the same directory name, they share a project bucket.
"""

import os
import subprocess


def _git_root():
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        root = out.stdout.strip()
        if root:
            return root
    except Exception:
        pass
    return None


def project_root():
    """Return the project root (git toplevel, else the current directory)."""
    return _git_root() or os.getcwd()


def project_name(root=None):
    """Return the readable project directory name."""
    root = root or project_root()
    return os.path.basename(os.path.normpath(root)) or "workspace"


def data_home():
    """Return the XDG data home (default ~/.local/share on macOS and Linux)."""
    return os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )


def workflow_dir():
    """Global workflow data directory containing all project buckets."""
    base = os.path.join(data_home(), "workflow-orchestrator")
    legacy = os.path.join(data_home(), "dev-workflow")
    if not os.path.exists(base) and os.path.exists(legacy):
        return legacy
    return base


def project_dir(name=None):
    """Readable ledger directory for the current or named project."""
    return os.path.join(workflow_dir(), name or project_name())


def project_dirs():
    """Sorted project directories; generated files at workflow root are excluded."""
    root = workflow_dir()
    try:
        names = os.listdir(root)
    except OSError:
        return []
    dirs = []
    for name in names:
        path = os.path.join(root, name)
        if name.startswith(".") or not os.path.isdir(path):
            continue
        dirs.append(path)
    return sorted(dirs)


def ledger_files(d=None):
    """Sorted ledger file names in one project directory."""
    d = d or project_dir()
    try:
        names = os.listdir(d)
    except OSError:
        return []
    return sorted(name for name in names if name.endswith(".json") and name != "tasks.json")


def default_ledger_path():
    """Current project's single ledger, or None when zero/multiple exist."""
    d = project_dir()
    files = ledger_files(d)
    return os.path.join(d, files[0]) if len(files) == 1 else None


def project_ledger_hint():
    d = project_dir()
    files = ledger_files(d)
    if files:
        return 'project ledgers under "%s": %s' % (d, ", ".join(files))
    return 'no project ledgers yet under "%s" (record one before rendering)' % d
