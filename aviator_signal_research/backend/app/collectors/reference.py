"""Collect official game documentation (rules, RTP, provably-fair description).

Output is a compact, de-duplicated JSON record per page with the source URL,
fetch time, content hash and extracted facts, plus a short ``context`` string
that can be handed to a person or a language model without page boilerplate.
Extracted facts are *candidates*: they never change the evaluation settings
automatically; a human confirms them (e.g. sets AVR_REFERENCE_RTP).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from scrapling.parser import Selector

from ..config import PROJECT_ROOT
from .browser import browser_context, rendered_html
from .http import FetchResult, fetch_static
from .policy import AccessChallenge, AccessPolicy, CollectorError, NetworkBlocked, detect_challenge, log



def reference_dir() -> Path:
    return Path(os.environ.get("AVR_REFERENCE_DIR", str(PROJECT_ROOT / "data" / "reference")))


BLOCK_TAGS = "h1, h2, h3, h4, p, li, td, th, dt, dd, blockquote, summary"
MIN_STATIC_TEXT = 400  # below this, the page is probably rendered client-side

RTP_RE = re.compile(r"(\d{2}(?:[.,]\d{1,2})?)\s?%")
MULT_RE = re.compile(r"(\d[\d,. ]*\d|\d)\s?[x×]\b", re.I)
KEYWORDS = {
    "rtp": re.compile(r"\bRTP\b|return to player", re.I),
    "max_multiplier": re.compile(r"\bmax(imum)?\b.*\bmultiplier|\bmultiplier\b.*\bmax(imum)?\b|\bup to\b", re.I),
    "limits": re.compile(r"\b(max(imum)?|min(imum)?)\s+(win|bet|stake|payout)\b", re.I),
    "provably_fair": re.compile(r"provabl|server[ -]?seed|client[ -]?seed|\bsha[- ]?(256|512)\b|\bhash(ed)?\b|\bnonce\b", re.I),
    "rules": re.compile(r"\bcash ?out\b|\bauto ?(bet|cash)|\bround\b.*\bcrash", re.I),
}
LINK_RE = re.compile(r"demo|play|provabl|fair|rules|help|how-to|terms|faq|guide", re.I)


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def text_blocks(html: str, url: str) -> tuple[str, list[str]]:
    sel = Selector(html, url=url)
    title = _norm(sel.css("title::text").get() or "")
    blocks, seen = [], set()
    for el in sel.css(BLOCK_TAGS):
        t = _norm(el.get_all_text(separator=" ", strip=True))
        if len(t) < 3 or t.lower() in seen:
            continue
        seen.add(t.lower())
        blocks.append(t)
    return title, blocks


def extract_links(html: str, url: str) -> list[dict]:
    sel = Selector(html, url=url)
    out, seen = [], set()
    for a in sel.css("a[href]"):
        href = (a.attrib.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "#")):
            continue
        text = _norm(a.get_all_text(separator=" ", strip=True))
        absolute = urljoin(url, href)
        if (LINK_RE.search(href) or LINK_RE.search(text)) and absolute not in seen:
            seen.add(absolute)
            out.append({"url": absolute, "text": text[:120]})
    for fr in sel.css("iframe[src]"):
        absolute = urljoin(url, fr.attrib.get("src"))
        if absolute not in seen:
            seen.add(absolute)
            out.append({"url": absolute, "text": "[iframe]"})
    return out


def extract_facts(blocks: list[str]) -> dict:
    facts: dict = {k: [] for k in KEYWORDS}
    for b in blocks:
        for key, kw in KEYWORDS.items():
            if not kw.search(b):
                continue
            entry: dict = {"excerpt": b[:400]}
            if key == "rtp":
                entry["values_percent"] = [float(v.replace(",", ".")) for v in RTP_RE.findall(b)
                                           if 80 <= float(v.replace(",", ".")) <= 100]
                if not entry["values_percent"]:
                    continue
            if key == "max_multiplier":
                vals = [float(v.replace(",", "").replace(" ", "")) for v in MULT_RE.findall(b)]
                entry["values_x"] = [v for v in vals if v >= 2]
                if not entry["values_x"]:
                    continue
            facts[key].append(entry)
    for key in facts:
        facts[key] = facts[key][:8]
    rtps = sorted({v for e in facts["rtp"] for v in e["values_percent"]})
    facts["summary"] = {
        "rtp_percent_candidates": rtps,
        "max_multiplier_candidates": sorted({v for e in facts["max_multiplier"] for v in e["values_x"]}),
        "mentions_provably_fair": bool(facts["provably_fair"]),
    }
    return facts


def build_context(record: dict, max_chars: int = 3000) -> str:
    """Compact, model-friendly text: facts first, then the relevant excerpts only."""
    s = record["facts"]["summary"]
    lines = [
        f"Source: {record['final_url']} (fetched {record['fetched_at']}, sha256 {record['sha256'][:12]})",
        f"Title: {record['title']}",
        f"RTP candidates (%): {s['rtp_percent_candidates'] or 'none found'}",
        f"Max multiplier candidates (x): {s['max_multiplier_candidates'] or 'none found'}",
        f"Provably-fair description present: {s['mentions_provably_fair']}",
    ]
    seen = set()
    for key in ("rtp", "max_multiplier", "limits", "provably_fair", "rules"):
        for e in record["facts"][key]:
            if e["excerpt"] not in seen:
                seen.add(e["excerpt"])
                lines.append(f"[{key}] {e['excerpt']}")
    out = "\n".join(lines)
    return out[:max_chars]


def _render(url: str, policy: AccessPolicy) -> tuple[str, str]:
    policy.wait_turn(url)
    with browser_context(policy.user_agent, target_url=url) as ctx:
        page = ctx.new_page()
        resp = page.goto(url, wait_until="domcontentloaded", timeout=policy.timeout_s * 1000)
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass
        html = rendered_html(page)
        status = resp.status if resp else 0
        marker = detect_challenge(html, status)
        if marker:
            raise AccessChallenge(f"{url} shows an anti-bot/CAPTCHA page ({marker!r}); stopped, not bypassed", url)
        return html, page.url


def collect_reference(url: str, policy: AccessPolicy | None = None, render: str = "auto",
                      out_dir: Path | None = None) -> dict:
    """Fetch, parse and store one documentation page. ``render``: auto | never | always."""
    policy = policy or AccessPolicy()
    out_dir = out_dir or reference_dir()
    res: FetchResult = fetch_static(url, policy)  # also enforces robots.txt
    html, final_url, method = res.text, res.final_url, "static"
    title, blocks = text_blocks(html, final_url)
    if render == "always" or (render == "auto" and sum(map(len, blocks)) < MIN_STATIC_TEXT):
        try:
            html, final_url = _render(url, policy)
            title, blocks = text_blocks(html, final_url)
            method = "rendered"
        except CollectorError:
            raise
        except Exception as exc:  # browser problems: keep the static result, say so
            log.warning("render fallback failed for %s: %s", url, exc)
            method = f"static (render failed: {type(exc).__name__})"
    content_sha = hashlib.sha256(html.encode()).hexdigest()
    record = {
        "source_url": url,
        "final_url": final_url,
        "fetched_at": res.fetched_at,
        "http_status": res.status,
        "method": method,
        "sha256": content_sha,
        "title": title,
        "facts": extract_facts(blocks),
        "links": extract_links(html, final_url),
        "n_text_blocks": len(blocks),
        "text": "\n".join(blocks)[:20000],
    }
    record["context"] = build_context(record)
    out_dir.mkdir(parents=True, exist_ok=True)
    host = urlsplit(final_url).netloc.replace(":", "_") or "page"
    path = out_dir / f"{host}_{content_sha[:12]}.json"
    record["unchanged"] = path.exists()
    if not record["unchanged"]:
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    record["stored_at"] = str(path)
    return record


def collect_many(urls: list[str], policy: AccessPolicy | None = None, render: str = "auto",
                 follow_doc_links: bool = True, max_pages: int = 6, out_dir: Path | None = None) -> dict:
    """Collect the given pages and (optionally) same-site documentation links they reference."""
    policy = policy or AccessPolicy()
    queue, done, results, errors = list(urls), set(), [], []
    while queue and len(done) < max_pages:
        u = queue.pop(0)
        if u in done:
            continue
        done.add(u)
        try:
            rec = collect_reference(u, policy, render, out_dir)
            results.append(rec)
            if follow_doc_links:
                base = urlsplit(rec["final_url"]).netloc
                for link in rec["links"]:
                    lu = link["url"]
                    if urlsplit(lu).netloc == base and re.search(r"provabl|fair|rules|help|faq", lu, re.I):
                        queue.append(lu)
        except NetworkBlocked as exc:
            errors.append(exc.to_dict())
            break  # same host will fail the same way
        except CollectorError as exc:
            errors.append(exc.to_dict())
    return {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "documents": [{k: r[k] for k in ("final_url", "title", "method", "sha256", "unchanged", "stored_at")}
                      | {"summary": r["facts"]["summary"], "links": r["links"][:20]} for r in results],
        "errors": errors,
        "context": "\n\n".join(r["context"] for r in results),
    }


def list_reference(out_dir: Path | None = None) -> list[dict]:
    out_dir = out_dir or reference_dir()
    out = []
    for p in sorted(out_dir.glob("*.json")):
        try:
            r = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        out.append({k: r.get(k) for k in ("final_url", "title", "fetched_at", "method", "sha256", "context")}
                   | {"summary": r.get("facts", {}).get("summary"), "links": r.get("links", [])[:30], "file": p.name})
    return out
