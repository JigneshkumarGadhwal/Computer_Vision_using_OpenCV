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

* robots.txt fetched and obeyed for every host (inaccessible robots.txt with 401/403 = full disallow);
  `Crawl-delay` is honoured.
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
python scripts/research_cli.py observe <public game page URL> --source spribe-demo-observed --minutes 60
```

* Checks robots.txt, opens the page once, then reads the **already-loaded DOM, including iframes**, every
  `--poll` seconds. No extra requests are sent to the server.
* Finds the recent-results bar automatically: the parent containing the most elements whose full text is a
  multiplier (`2.31x`, `<span>2.31</span>x`). Other players' cash-out tables and balances are ignored. The bar's
  XPath is pinned. If it disappears, detection re-runs and a `selector_relocated` event is logged.
  `--selector` overrides with a CSS selector.
* `new_since` aligns each snapshot with the previous one (≥3 overlapping values) and emits only the new rounds,
  oldest first. If they cannot be aligned, a `gap` is logged and nothing is guessed.
* Each new round is written to `data/raw/observations/*.jsonl` (raw) and ingested through the normal path as
  source kind `public_observation`: `verified=False`, timestamp = observation time, no official round id.
  WebSocket frames are logged raw (text ≤2 KB, binary ≤1 KB as base64; max 20,000 frames) for later protocol
  analysis; they are **not decoded**.
* Stops on: time / round budget, CAPTCHA/anti-bot page, robots.txt disallow, network refusal, repeated page
  failures, or no new round for 5 minutes.
* Rounds visible at page load are **not** ingested (their times are unknown); counting starts with the first new round.

### Limitations of observed data

* A vendor's **demo** page may use a separate server and may not reflect real-money rounds at any operator.
  Treat it as its own data source; never mix it with an operator export.
* Without round ids, a missed poll window longer than the bar length causes a `gap` (logged, not filled).
* Repeating identical values in the bar can, in rare cases, be aligned ambiguously; ≥3 overlapping values are required.
* Terms of use: check the site's terms yourself before running a long observation; robots.txt compliance is
  necessary but not sufficient.

## Verified in this environment (2026-10-21)

| Check | Result |
|---|---|
| Local fixture site: documentation facts, link following, de-duplication, compact context | pass |
| robots.txt disallow, challenge page, 503 retry, 404 no-retry, unreachable host, rate limit | pass |
| JS-only page → rendered fallback | pass |
| Live observer, game in an iframe fed by a real WebSocket server: observed rounds equal the exact server sequence, in order, no gaps; WebSocket frames logged; ingestion into DB as `public_observation` | pass |
| Observer stops on CAPTCHA page and on robots.txt disallow | pass |
| **Real target `https://spribe.co/games/aviator`** | **not reachable**: the environment's network policy refuses the host (proxy `CONNECT` 403). Both `collect-docs` and `observe` report `network_blocked` and exit with code 2 |

Two bugs found while building this were fixed:
* `add_single_round` gave every manual/live round after the first sequence 0 (regression test added).
* Chromium ignores loopback proxy bypass rules, so the proxy is now chosen per target host.
