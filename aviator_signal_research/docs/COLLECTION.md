# Data collection: official documentation and publicly observable rounds

## Tool decision (evaluated 2026-10-21)

| Candidate | Verdict | Evidence |
|---|---|---|
| **Scrapling 0.5 — core parser** (`scrapling.parser.Selector`) | **Used** for documentation parsing and user-supplied CSS selectors | Light dependencies (lxml, cssselect, orjson, tld, w3lib); fast CSS/XPath with clean text extraction |
| Scrapling `find_by_regex` / `find_similar` for the results bar | Not used | Tested: `find_by_regex` matches an element's *own* text only, so it misses `<div><span>2.31</span>x</div>`, which is common in game UIs. An lxml walk over full element text handles both forms (tested) |
| Scrapling `[fetchers]` extra (`StealthyFetcher`, Cloudflare solving) | **Rejected** | Pulls `patchright` (stealth-patched Playwright), `browserforge` and `apify-fingerprint-datapoints` (fingerprint spoofing). Bypassing anti-bot protection is out of scope by design |
| **httpx** (already a dependency) | **Used** for static pages | Honours the environment proxy / `NO_PROXY`, simple retry control |
| **Official Playwright 1.56** + preinstalled Chromium | **Used** for JS-rendered pages and live observation | A long-lived page with DOM reads and WebSocket logging; one-shot fetchers cannot watch rounds arrive |
| Scrapy | Not used | Built for large crawls; adds a framework for ~a few pages |

## What is enforced (never bypassed)

* robots.txt fetched and obeyed for every host, including the final host after redirects, following RFC 9309:
  4xx (incl. 401/403) = no rules, 5xx/429/unreachable = full disallow. `AVR_ROBOTS_4XX=disallow` makes 4xx a full
  disallow. `Crawl-delay` is honoured. (An earlier version treated 401/403 as full disallow and wrongly cited the RFC
  for it; corrected 2026-10-21.)
* Minimum interval between requests per host (default 5 s, `AVR_COLLECTOR_MIN_INTERVAL`).
* Honest User-Agent (`AVR_COLLECTOR_USER_AGENT`).
* Retries with exponential backoff only for 429/5xx and transient connection errors; `Retry-After` respected.
  4xx is not retried.
* Anti-bot interstitials / CAPTCHAs (Cloudflare challenge, reCAPTCHA, hCaptcha, Turnstile) **stop collection**
  with `access_challenge`.
* Network-policy refusals (proxy `CONNECT` 403, DNS failure) are reported as `network_blocked` immediately.

## 1. Official documentation (`collect-docs`, `POST /api/reference/collect`)

```bash
python scripts/research_cli.py collect-docs https://spribe.co/games/aviator
```

* Static fetch first; if the page has < 400 characters of text it is rendered in Chromium (`--render auto|never|always`).
* Follows same-site links that look like rules / provably-fair / help pages (max 6 pages).
* Output per page, in `data/reference/<host>_<sha12>.json`: source URL, final URL, fetch time, HTTP status,
  content SHA-256, title, de-duplicated text blocks, links (demo/rules/fairness/terms/iframes) and **candidate facts**:
  RTP percentages (only 80–100%), maximum multipliers, limit statements and provably-fair excerpts.
* `context`: a ≤3,000-character, boilerplate-free summary (facts first, then only the relevant excerpts) that can be
  given to a person or a language model without wasting tokens.
* Identical content is stored once (`unchanged: true` on re-collection).
* Facts are **never applied automatically**. A human confirms them, e.g. by setting `AVR_REFERENCE_RTP`.

## 2. Publicly observable rounds (`observe`)

```bash
python scripts/research_cli.py observe --profile spribe --confirm-legal-age --verify-fairness \
       --source spribe-demo-observed --minutes 60
```

Two independent channels are read from the one open page; no extra requests are made except the optional
per-round fairness lookup.

1. **Game messages (primary).** The game talks SmartFoxServer 2X over its own WebSocket. `sfs2x.py` decodes the
   binary SFSObject format (no third-party SDK). Only round-level messages are used: `changeState` (betting /
   flight, official round id, server time) and `roundChartInfo` (official round id + crash multiplier).
   Round time = server time at which the flight started.
2. **History bar (cross-check / fallback).** Auto-detected as the parent with the most multiplier-text children
   (`<div>1.50x</div>`, `<span>2.31</span>x`), XPath-pinned, re-located if the markup changes; snapshots are aligned
   with `new_since` (gaps logged, never guessed).

`Reconciler` makes the WebSocket authoritative when decodable results arrive (else falls back to the history bar)
and reports `confirmed / ws_only / dom_only`. Betting phases without a result are logged as `round_without_result`
and are **not** recorded as rounds.

**`--verify-fairness`** opens each round's provably-fair record in the game (as a player can) after the next
betting phase begins — the game ignores clicks right after a crash — reads the `roundFairnessResponse`, re-derives
the result with the verified formula (METHODOLOGY §3) and stores the revealed server seed and client seeds on the
round; the round is marked `verified=True` only if hash and result match.

**Privacy.** Game messages include other players' usernames, avatars and bets. Raw frames are **not stored**
(opt-in `--log-raw-frames` for protocol debugging); only round-level fields (ids, states, multipliers, seeds) are
written to `data/raw/observations/*.jsonl`. Tests assert that player names never reach the log.

Stops on: time / round budget, CAPTCHA/anti-bot page, robots.txt disallow (start host *and* redirect target),
network refusal, repeated page failures, or no new round for 5 minutes.

### Limitations of observed data

* The **demo** server may differ from real-money operators (different RTP configuration, different traffic).
  Keep it as its own source; never mix it with an operator export.
* Official round ids are **not consecutive** (the counter is shared), so id gaps are not missing rounds.
* When the game's WebSocket reconnects, a round can be missed on that channel (observed: 2 of 304 rounds in one
  110-minute chunk, both still seen on the history bar and counted as `dom_only`). In WebSocket mode such rounds
  are not ingested, so strict round-to-round adjacency is broken at those points; the counts are in each run's summary.
* Multipliers ≥ 1,000x are displayed with thousands separators (`6,516.99x`); handled since 2026-10-21 21:00 UTC
  (one earlier round of 6,516.99x was recorded from the WebSocket but could not be fairness-verified).
* Pre-round server-seed hashes ("next server seed SHA256") are not yet recorded, so commitment checks are not run.
* Terms of use: none were found on spribe.co beyond privacy/cookie policies; check before long observations.

## Verified in this environment (2026-10-21)

| Check | Result |
|---|---|
| Local fixture site: documentation facts, link following, de-duplication, compact context | pass |
| robots.txt disallow, challenge page, 503 retry, 404 no-retry, unreachable host, rate limit | pass |
| JS-only page → rendered fallback | pass |
| Live observer, game in an iframe fed by a real WebSocket server: observed rounds equal the exact server sequence, in order, no gaps; WebSocket frames logged; ingestion into DB as `public_observation` | pass |
| Observer stops on CAPTCHA page and on robots.txt disallow | pass |
| **Real target `https://spribe.co/games/aviator`** (after `spribe.co` was allowed) | **collected** (rendered; Angular app). Official text: *"Return to Player (RTP) 97%"*, game type *Mini Game*, devices *Desktop, Tablet, Mobile*. No provably-fair formula or round history is published on the page. `robots.txt` is not a real robots file (the app returns its HTML for every path) → no crawl rules |
| Demo game URL | found in spribe.co's public JS bundle: `https://demo.spribe.io/launch/aviator?currency=USD&lang=EN&return_url=https://spribe.co/games`, opened by script after an **"I'm over 18"** confirmation → stored in `app/collectors/profiles.py`; `observe --profile spribe` requires `--confirm-legal-age` |
| Demo launcher `demo.spribe.io` (after `spribe.io` was allowed) | `/launch/aviator` answers **302** to `https://aviator-demo.spribegaming.com/?currency=USD&operator=demo&jurisdiction=CW&...&token=…` (a fresh demo session). Its `robots.txt` returns a CloudFront **403** "Request blocked" → per RFC 9309 no rules |
| Game host `aviator-demo.spribegaming.com` (after `spribegaming.com` was allowed) | robots.txt is the app's HTML (no rules); page has `<meta name="robots" content="noindex, nofollow">` (indexing directive; nothing is indexed or followed). Game data over `wss://app-demo.spribegaming.com/BlueBox/websocket` (SmartFoxServer 2X) |
| In-game documentation | Game rules, "What is Provably Fair", Game limits (demo: bet 0.10–100 USD, max win 10,000 USD) collected; text quoted in METHODOLOGY §3 |
| Seed → multiplier formula | reconstructed and reproduced **16/16** published rounds (hash, 13-hex prefix, result) |
| Live observation, 5 min | 13 rounds, history bar confirmed **13/13**, 0 decode errors, 0 gaps, 1 betting phase without a result |
| Live observation with `--verify-fairness`, 6 min | **17 rounds, 17/17 re-derived from revealed seeds and matching**, history bar confirmed 17/17 |
| Bugs found on the live game and fixed | bubble added after the crash animation; history clicks ignored right after a crash (now waits for the next betting phase); both reproduced in the fixture tests |

Fixes made after running against the real site: label/value pairs (`<p>Return to Player (RTP)</p><h5>97%</h5>`)
are now paired; buttons that open links by script are recorded.

Two bugs found while building this were fixed:
* `add_single_round` gave every manual/live round after the first sequence 0 (regression test added).
* Chromium ignores loopback proxy bypass rules, so the proxy is now chosen per target host.
