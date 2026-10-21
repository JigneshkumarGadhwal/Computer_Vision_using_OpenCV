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
  if (typeof ev.data !== 'string') return;  // binary game-protocol messages are not for the UI here
  const m = JSON.parse(ev.data);
  const h = document.getElementById('hist');
  const d = document.createElement('div'); d.className = 'bubble';
  d.onclick = () => ws.send('fair:' + m.rid);
  const [a, b] = m.crash.toFixed(2).split('.');
  d.innerHTML = '<span>' + a + '.' + b + '</span>x';   // value split across nodes on purpose
  setTimeout(() => {  // like the real game: the bubble appears after the crash animation
    h.prepend(d);
    while (h.children.length > 20) h.removeChild(h.lastChild);
  }, __DOM_DELAY_MS__);
};
</script></body></html>"""


def sfs_encode(obj) -> bytes:
    """Tiny SFS2X SFSObject encoder for tests (subset: str, int, long, double, bool, list, dict)."""
    import struct

    def val(v):
        if isinstance(v, bool):
            return b"\x01" + bytes([1 if v else 0])
        if isinstance(v, int):
            return (b"\x04" + struct.pack(">i", v)) if -2**31 <= v < 2**31 else (b"\x05" + struct.pack(">q", v))
        if isinstance(v, float):
            return b"\x07" + struct.pack(">d", v)
        if isinstance(v, str):
            e = v.encode()
            return b"\x08" + struct.pack(">H", len(e)) + e
        if isinstance(v, list):
            return b"\x11" + struct.pack(">H", len(v)) + b"".join(val(x) for x in v)
        if isinstance(v, dict):
            out = b"\x12" + struct.pack(">H", len(v))
            for k, x in v.items():
                ek = k.encode()
                out += struct.pack(">H", len(ek)) + ek + val(x)
            return out
        raise TypeError(type(v))

    body = val(obj)
    return bytes([0x80]) + struct.pack(">H", len(body)) + body


def extension(cmd: str, params: dict) -> bytes:
    return sfs_encode({"c": 1, "a": 13, "p": {"c": cmd, "r": -1, "p": params}})


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
    def __init__(self, ws_interval: float = 0.3, n_rounds: int = 400, sfs2x: bool = False,
                 drop_result_for: set[int] | None = None, dom_delay: float = 0.0):
        self.requests: list[str] = []
        self.flaky_hits = 0
        self.sent: list[float] = []
        self.ws_interval = ws_interval
        self.sequence = crash_sequence(n_rounds)
        self.dom_delay = dom_delay  # seconds between the game message and the history bubble appearing
        self.sfs2x = sfs2x  # also send SFS2X binary round messages (like the real game)
        self.drop_result_for = drop_result_for or set()  # round indexes whose result message is withheld
        self.round_ids: list[int] = []
        self.fairness: dict[int, dict] = {}
        self.fairness_requests = 0
        self.ignored_fair_requests = 0
        self.result_sent_at: dict[int, float] = {}
        self.ignore_fair_s = 0.0  # fairness requests this soon after a result are ignored
        self.tamper_result_for: set[int] = set()  # round indexes whose published fairness result is altered
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
                    return self._send(200, GAME_FRAME.replace("__WS_PORT__", str(site.ws_port))
                                      .replace("__DOM_DELAY_MS__", str(int(site.dom_delay * 1000))))
                return self._send(404, "not found")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self.ws_port = 0

    async def _ws_main(self):
        import websockets

        async def receiver(conn):
            async for msg in conn:
                if isinstance(msg, str) and msg.startswith("fair:"):
                    rid = int(msg[5:])
                    f = self.fairness.get(rid)
                    import time as _t

                    if f and _t.monotonic() - self.result_sent_at.get(rid, 0) < self.ignore_fair_s:
                        self.ignored_fair_requests += 1  # like the live game right after a crash
                        continue
                    if f:
                        self.fairness_requests += 1
                        await conn.send(extension("roundFairnessResponse", {"code": 200, "fairness": f}))

        async def handler(conn):
            recv_task = asyncio.ensure_future(receiver(conn))
            i = len(self.sent)
            try:
                while i < len(self.sequence):
                    await asyncio.sleep(self.ws_interval)
                    v = self.sequence[i]
                    rid = 5000 + 3 * i  # non-consecutive ids, like the real server
                    if self.sfs2x:
                        v = self._seeded_round(rid, i)
                        import time as _t

                        now_ms = int(_t.time() * 1000)
                        await conn.send(extension("changeState", {"newStateId": 1, "code": 200, "roundId": rid,
                                                                   "betStateEndTime": now_ms, "serverTime": now_ms}))
                        await conn.send(extension("updateCurrentBets", {"bets": {"username": "demo_1"}}))  # noise
                        if i not in self.drop_result_for:
                            await conn.send(extension("roundChartInfo", {"code": 200, "maxMultiplier": v, "roundId": rid}))
                            self.result_sent_at[rid] = _t.monotonic()
                    await conn.send(json.dumps({"round": i, "crash": v, "rid": rid}))
                    self.sent.append(v)
                    self.round_ids.append(rid)
                    i += 1
            except Exception:
                return
            finally:
                recv_task.cancel()

        self._ws_server = await websockets.serve(handler, "127.0.0.1", 0)
        self.ws_port = self._ws_server.sockets[0].getsockname()[1]
        self._ready.set()
        await self._ws_server.wait_closed()

    def _seeded_round(self, rid: int, i: int) -> float:
        """Generate a round exactly like the verified Spribe derivation (random seeds -> SHA-512 -> result)."""
        import random

        from app.analysis.fairness import spribe_aviator_result, spribe_hash

        rng = random.Random(rid)
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        server = "".join(rng.choice(alphabet) for _ in range(40))
        clients = ["".join(rng.choice(alphabet) for _ in range(20)) for _ in range(3)]
        digest = spribe_hash(server, clients)
        v = spribe_aviator_result(digest)
        published = v + 1.0 if i in self.tamper_result_for else v
        self.fairness[rid] = {"result": published, "seedSHA256": digest, "roundId": rid, "serverSeed": server,
                              "playerSeeds": [{"seed": c, "username": "demo_secret_user"} for c in clients]}
        return v

    def __enter__(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        threading.Thread(target=lambda: self._loop.run_until_complete(self._ws_main()), daemon=True).start()
        self._ready.wait(10)
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self._loop.call_soon_threadsafe(self._ws_server.close)
