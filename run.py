#!/usr/bin/env python3
"""Start the local server and open the GUI in the default browser."""
import socket
import sys
import threading
import time
import webbrowser

HOST, PORT = "127.0.0.1", 8000


def _free_port(start):
    for p in range(start, start + 20):
        with socket.socket() as s:
            if s.connect_ex((HOST, p)) != 0:
                return p
    raise SystemExit("No free port found near %d" % start)


def main():
    try:
        import uvicorn
        import app.main  # noqa: F401  (fails early with a clear message if deps are missing)
    except ImportError as exc:
        sys.exit(f"Missing dependency: {exc}\nRun: pip install -r requirements.txt")
    port = _free_port(PORT)
    url = f"http://{HOST}:{port}"
    server = uvicorn.Server(uvicorn.Config("app.main:app", host=HOST, port=port, log_level="warning"))

    def open_when_ready():
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.1)
        print(f"PDF → EPUB converter running at {url}  (Ctrl+C to quit)")
        webbrowser.open(url)

    threading.Thread(target=open_when_ready, daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
