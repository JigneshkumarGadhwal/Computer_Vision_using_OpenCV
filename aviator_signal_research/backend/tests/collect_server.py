"""Local fixture site for collector tests (no external network).

Serves documentation pages, robots.txt, an anti-bot challenge page, a flaky
endpoint, a JS-rendered page, and a crash-game page whose history bar lives in
an iframe and is fed by a real WebSocket server with a known round sequence.
"""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DOC_PAGE = """<!doctype html><html><head><title>Crash Game — Rules (TEST FIXTURE)</title></head><body>
<nav><a href="/">Home</a> <a href="/games/crash">Games</a> <a href="/terms">Terms of use</a></nav>
<h1>Crash Game</h1>
<p>The multiplier grows until the plane flies away. Cash out before the crash.</p>
<p>The game's theoretical RTP is 97%.</p>
<p>Maximum multiplier: up to 10,000x per round.</p>
<p>Maximum win: 10,000 USD per bet.</p>
<ul><li>Auto cash out is available.</li><li>Auto cash out is available.</li></ul>
<p>The game is Provably Fair: the server seed is hashed with SHA-256 before the round and combined with
three client seeds; the result is derived from a SHA-512 hash.</p>
<a href="/provably-fair">How provably fair works</a> <a href="/demo">Play demo</a>
<footer><a href="/terms">Terms of use</a></footer></body></html>"""

FAIR_PAGE = """<!doctype html><html><head><title>Provably Fair (TEST FIXTURE)</title></head><body>
<h2>Provably fair</h2><p>Before each round the server seed hash is shown. After the round the server seed
is revealed; the SHA-512 hash of server seed and client seeds determines the result.</p></body></html>"""

JS_PAGE = """<!doctype html><html><head><title>Rendered (TEST FIXTURE)</title></head><body><div id=app></div>
<script>document.getElementById('app').innerHTML =
 '<h1>Rendered rules</h1><p>The RTP of this game is 97.5%.</p>' + '<p>' + 'Lorem ipsum filler text. '.repeat(30) + '</p>';
</script></body></html>"""

CHALLENGE = """<!doctype html><html><head><title>Just a moment...</title></head><body>
<div id="cf-chl-widget">Checking your browser</div></body></html>"""

GAME_OUTER = """<!doctype html><html><head><title>Game (TEST FIXTURE)</title></head><body>
<div class="balance">1000.00 USD</div><iframe src="/game-frame" width=800 height=400></iframe></body></html>"""

GAME_FRAME = """<!doctype html><html><body>
<div class="top"><div class="hist" id="hist"></div></div>
<table class="bets"><tr><td>user1</td><td>2.00x</td></tr><tr><td>user2</td><td>1.50x</td></tr>
<tr><td>user3</td><td>3.10x</td></tr></table>
<script>
const ws = new WebSocket('ws://127.0.0.1:__WS_PORT__/');
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  const h = document.getElementById('hist');
  const d = document.createElement('div'); d.className = 'bubble';
  const [a, b] = m.crash.toFixed(2).split('.');
  d.innerHTML = '<span>' + a + '.' + b + '</span>x';   // value split across nodes on purpose
  h.prepend(d);
  while (h.children.length > 20) h.removeChild(h.lastChild);
};
</script></body></html>"""


def crash_sequence(n: int, seed: int = 1) -> list[float]:
    """Deterministic multipliers with repeats (1.00x is common) to stress alignment."""
    x = seed
    out = []
    for _ in range(n):
        x = (1103515245 * x + 12345) % (2 ** 31)
        u = (x + 1) / (2 ** 31 + 1)
        out.append(max(1.0, int(97 / u) / 100))
    return out


class FixtureSite:
    def __init__(self, ws_interval: float = 0.3, n_rounds: int = 400):
        self.requests: list[str] = []
        self.flaky_hits = 0
        self.sent: list[float] = []
        self.ws_interval = ws_interval
        self.sequence = crash_sequence(n_rounds)
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="text/html; charset=utf-8"):
                data = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # noqa: N802
                site.requests.append(self.path)
                p = self.path
                if p == "/robots.txt":
                    return self._send(200, "User-agent: *\nDisallow: /private\nDisallow: /blocked-game\n", "text/plain")
                if p == "/games/crash":
                    return self._send(200, DOC_PAGE)
                if p == "/provably-fair":
                    return self._send(200, FAIR_PAGE)
                if p == "/js-only":
                    return self._send(200, JS_PAGE)
                if p in ("/challenge", "/game-challenge"):
                    return self._send(403 if p == "/challenge" else 200, CHALLENGE)
                if p == "/flaky":
                    site.flaky_hits += 1
                    return self._send(503, "busy") if site.flaky_hits == 1 else self._send(200, DOC_PAGE)
                if p in ("/game", "/blocked-game"):
                    return self._send(200, GAME_OUTER)
                if p == "/game-frame":
                    return self._send(200, GAME_FRAME.replace("__WS_PORT__", str(site.ws_port)))
                return self._send(404, "not found")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self.ws_port = 0

    async def _ws_main(self):
        import websockets

        async def handler(conn):
            i = len(self.sent)
            try:
                while i < len(self.sequence):
                    await asyncio.sleep(self.ws_interval)
                    v = self.sequence[i]
                    await conn.send(json.dumps({"round": i, "crash": v}))
                    self.sent.append(v)
                    i += 1
            except Exception:
                return

        self._ws_server = await websockets.serve(handler, "127.0.0.1", 0)
        self.ws_port = self._ws_server.sockets[0].getsockname()[1]
        self._ready.set()
        await self._ws_server.wait_closed()

    def __enter__(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        threading.Thread(target=lambda: self._loop.run_until_complete(self._ws_main()), daemon=True).start()
        self._ready.wait(10)
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self._loop.call_soon_threadsafe(self._ws_server.close)
