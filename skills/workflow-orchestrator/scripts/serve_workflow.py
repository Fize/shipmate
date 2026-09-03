#!/usr/bin/env python3
"""serve_workflow.py — live workflow dashboard server.

Zero-dependency (Python 3.9+ standard library only) HTTP server that reads
workflow ledgers directly from disk on every request — no static pre-rendering.
The dashboard reflects the current on-disk state, including per-step progress,
and refreshes automatically in the browser.

Endpoints:
  GET /                              live HTML dashboard (templates/live.html)
  GET /api/index                     global task index (projects + tasks + steps);
                                     optional ?from=YYYY-MM-DD&to=YYYY-MM-DD filters
                                     tasks by start date (inclusive, local time)
  GET /api/ledger/<project>/<file>   full ledger + derived step progress
  GET /api/health                    {"ok": true}

Usage:
  python3 serve_workflow.py [--host 127.0.0.1] [--port 18929]
"""

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import _workflow_paths
from _workflow_steps import derive_step_progress
from index_workflow import build_index, summarize

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(HERE, "..", "templates")

DEFAULT_HOST = "127.0.0.1"
# Uncommon port, outside the range of typical dev servers (8000/8080/3000/5000)
# and below the ephemeral port range (49152+), to avoid conflicts.
DEFAULT_PORT = 18929


class Handler(BaseHTTPRequestHandler):
    server_version = "dev-workflow/2.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    # --- helpers -----------------------------------------------------------
    def _send(self, status, content_type, body_bytes):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body_bytes)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body_bytes)

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", body)

    def _send_text(self, text, status=200, content_type="text/plain; charset=utf-8"):
        self._send(status, content_type, text.encode("utf-8"))

    def _send_file(self, path, content_type):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = f.read().encode("utf-8")
        except OSError:
            self._send_text("not found", status=404)
            return
        self._send(200, content_type, data)

    # --- routes ------------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path in ("/", "/index.html"):
            self._send_file(os.path.join(TEMPLATE_DIR, "live.html"),
                            "text/html; charset=utf-8")
            return

        if path == "/api/health":
            self._send_json({"ok": True, "time": time.time()})
            return

        if path == "/api/index":
            q = parse_qs(parsed.query)
            def _first(key):
                vals = q.get(key)
                return vals[0] if vals else None
            self._send_json(build_index(date_from=_first("from"), date_to=_first("to")))
            return

        if path.startswith("/api/ledger/"):
            rel = path[len("/api/ledger/"):].strip("/")
            if not rel or "/" not in rel:
                self._send_json({"error": "expected /api/ledger/<project>/<file>"}, status=400)
                return
            project, filename = rel.split("/", 1)
            ledger_path = os.path.join(_workflow_paths.project_dir(project), filename)
            if not os.path.isfile(ledger_path):
                self._send_json({"error": "ledger not found", "project": project, "file": filename},
                                status=404)
                return
            try:
                with open(ledger_path, "r", encoding="utf-8") as f:
                    ledger = json.load(f)
            except Exception as e:
                self._send_json({"error": "cannot read ledger: %s" % e}, status=500)
                return
            summary = summarize(ledger_path, project) or {}
            events = ledger.get("events") if isinstance(ledger.get("events"), list) else []
            work_item = ledger.get("work_item") or {}
            progress = derive_step_progress(events, work_item)
            summary["steps"] = progress["steps"]
            summary["current_step"] = progress["current_step"]
            summary["ledger"] = ledger
            self._send_json(summary)
            return

        self._send_json({"error": "not found"}, status=404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()


def main(argv):
    host = DEFAULT_HOST
    port = DEFAULT_PORT
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--host", "-H"):
            i += 1
            if i < len(argv):
                host = argv[i]
        elif a in ("--port", "-p"):
            i += 1
            if i < len(argv):
                port = int(argv[i])
        elif a in ("--help", "-h"):
            print(__doc__)
            return 0
        i += 1

    server = ThreadingHTTPServer((host, port), Handler)
    url = "http://%s:%d/" % (host, port)
    print("dev-workflow dashboard: %s" % url)
    print("data root: %s" % _workflow_paths.workflow_dir())
    print("Ctrl-C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
