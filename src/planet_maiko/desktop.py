"""Launch Planet Maiko as a desktop application using pywebview."""

import os
import subprocess
import sys
import threading
import time
import logging

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8420
VITE_PORT = 5173


def main(host=DEFAULT_HOST, port=DEFAULT_PORT):
    import webview
    from planet_maiko.app import create_app

    app = create_app(start_scheduler=True)

    # Run Flask in a daemon thread so it dies when the window closes
    server = threading.Thread(
        target=lambda: app.run(host=host, port=port, use_reloader=False),
        daemon=True,
    )
    server.start()
    _wait_for_server(host, port)

    # Start the Vite dev server
    frontend_dir = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
    frontend_dir = os.path.normpath(frontend_dir)
    vite_proc = None

    if os.path.isdir(frontend_dir):
        vite_proc = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=frontend_dir,
            shell=True,
        )
        _wait_for_server("localhost", VITE_PORT)
        url = f"http://localhost:{VITE_PORT}"
        logger.info("Vite dev server started on %s", url)
    else:
        # Fallback: serve pre-built static files from Flask
        url = f"http://{host}:{port}"
        logger.info("Frontend dir not found — using Flask static files at %s", url)

    try:
        webview.create_window("Planet Maiko", url, width=1280, height=800)
        webview.start()
    finally:
        if vite_proc is not None:
            vite_proc.terminate()
            vite_proc.wait(timeout=5)


def _wait_for_server(host, port, timeout=30):
    """Block until a server is returning HTTP responses."""
    import urllib.request
    import urllib.error

    url = f"http://{host}:{port}/"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except urllib.error.HTTPError:
            # Any HTTP response (even 404) means the server is up
            return
        except (urllib.error.URLError, OSError):
            time.sleep(0.3)
    logger.warning("Server on port %d did not respond within %ds", port, timeout)


if __name__ == "__main__":
    main()
