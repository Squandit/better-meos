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

# Worker threads per server. Each open live page holds one (see events.py), so
# these sit well above events.MAX_STREAMS to leave room for normal requests.
PUBLIC_THREADS = 32
ADMIN_THREADS = 24


def main() -> None:
    import app as appmod
    import config
    import si_reader
    from waitress import serve

    admin_port = config.admin_port()
    public_port = config.public_port()

    # The console answers other computers only when the operator turns that on
    # in Settings (and security.py still requires an admin password for it).
    admin_host = "0.0.0.0" if config.get("admin_lan") else "127.0.0.1"

    si_reader.start_all()  # real SI reader if configured; else no-op

    # Public/results server on its own port, in the background. Every phone
    # watching live results holds one thread (capped by events.MAX_STREAMS), so
    # the pool is sized well above that cap.
    threading.Thread(
        target=serve,
        kwargs={"app": appmod.app, "host": "0.0.0.0", "port": public_port,
                "threads": PUBLIC_THREADS},
        daemon=True,
    ).start()

    url = f"http://127.0.0.1:{admin_port}/start"
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    print(f"\n  better-meos admin console:  {url}")
    print(f"  Public entry + results:     http://127.0.0.1:{public_port}/results")
    print(f"  Same-WiFi devices: http://<this-PC-IP>:{public_port}")
    if admin_host == "127.0.0.1":
        print("  (The admin console only answers on this PC. See Settings to change.)\n")
    else:
        print(f"  Admin console on the LAN: http://<this-PC-IP>:{admin_port}\n")

    # Admin server in the foreground (blocks).
    serve(appmod.app, host=admin_host, port=admin_port, threads=ADMIN_THREADS)


if __name__ == "__main__":
    main()
