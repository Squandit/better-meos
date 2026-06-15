"""
Operator launcher: serve better-meos and open the browser at the start page.

Used by the packaged Windows .exe (and ``python launcher.py``). Serves with
waitress -- a real WSGI server -- instead of Flask's dev reloader, binds to the
LAN so phones on the same WiFi can reach it, and opens the browser to /start.

Two ports are served from the one process (sharing the in-memory event store):
  * the **admin** port (default 8799) -- the full operator console;
  * the **public** port (default 8800) -- only results + the entry form.
A request is routed to the right surface by the port it arrived on (see
``security.py``), so sharing the public URL never exposes event editing.
"""

from __future__ import annotations

import threading
import webbrowser


def main() -> None:
    import app as appmod
    import config
    import si_reader
    from waitress import serve

    admin_port = config.admin_port()
    public_port = config.public_port()

    si_reader.start_all()  # real SI reader if BMEOS_READER is set; else no-op

    # Public/results server on its own port, in the background.
    threading.Thread(
        target=serve,
        kwargs={"app": appmod.app, "host": "0.0.0.0", "port": public_port, "threads": 8},
        daemon=True,
    ).start()

    url = f"http://127.0.0.1:{admin_port}/start"
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    print(f"\n  better-meos admin console:  {url}")
    print(f"  Public entry + results:     http://127.0.0.1:{public_port}/results")
    print(f"  Same-WiFi devices: http://<this-PC-IP>:{public_port}\n")

    # Admin server in the foreground (blocks).
    serve(appmod.app, host="0.0.0.0", port=admin_port, threads=8)


if __name__ == "__main__":
    main()
