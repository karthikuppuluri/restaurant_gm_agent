"""
plumbing/launcher.py — run all plumbing listeners + the worker in ONE container.

Cloud Run's worker service runs this as its command (same image as the app,
different entrypoint). Each listener runs as a subprocess and is restarted on
exit; a stdlib HTTP server answers on $PORT because Cloud Run requires the
container to listen even when the real work is change-stream driven.

Local equivalent: ./start.sh (separate processes with per-service logs).
"""

import http.server
import os
import subprocess
import sys
import threading
import time

SERVICES = [
    "plumbing.depletion",
    "plumbing.rollups",
    "plumbing.replenishment",
    "plumbing.reconcile",
    "plumbing.worker",
]


def _health_server() -> None:
    port = int(os.environ.get("PORT", 8080))

    class Health(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):  # keep logs to the listeners
            pass

    http.server.HTTPServer(("0.0.0.0", port), Health).serve_forever()


def _run_forever(module: str) -> None:
    while True:
        print(f"[launcher] starting {module}", flush=True)
        proc = subprocess.Popen([sys.executable, "-u", "-m", module])
        proc.wait()
        print(f"[launcher] {module} exited {proc.returncode} — restarting in 5s",
              flush=True)
        time.sleep(5)


def main() -> None:
    threading.Thread(target=_health_server, daemon=True).start()
    threads = [threading.Thread(target=_run_forever, args=(m,), daemon=True)
               for m in SERVICES]
    for t in threads:
        t.start()
        time.sleep(2)  # stagger startups (worker loads the agents last)
    while True:  # keep the main thread alive
        time.sleep(60)


if __name__ == "__main__":
    main()
