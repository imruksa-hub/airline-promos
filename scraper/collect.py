"""Collect airline promotions and lucky draws into site/data/promos.json.

Python standard library only. Run:  python scraper/collect.py

For each source in sources.json the script:
  1. checks robots.txt and skips the page if crawling is not allowed,
  2. downloads the airline's promotions page,
  3. keeps links on the airline's own site that look like a promotion or lucky draw,
  4. reads the title, image and description of each linked page (a few per airline),
  5. merges the result with manual.json (hand-added items, e.g. Facebook posts)
     and with the previous run, so we know when each item was first seen.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "sources.json"
MANUAL = ROOT / "manual.json"
OUTPUT = ROOT / "site" / "data" / "promos.json"

USER_AGENT = "AirlinePromosBot/0.1 (+https://github.com/; personal promotion aggregator)"
TIMEOUT = 25
DELAY_SECONDS = 2.0          # pause between requests to the same airline
MAX_DETAIL_PAGES = 12        # detail pages read per airline per run
KEEP_MISSING_DAYS = 1        # keep an item this long after it disappears from the airline page
BKK = timezone(timedelta(hours=7))
DEBUG = "--debug" in sys.argv

PROMO_WORDS = [
    "promo", "promotion", "deal", "offer", "sale", "discount", "special", "fare",
    "โปร", "ลด", "ราคาพิเศษ", "ข้อเสนอ", "ส่วนลด", "ดีล",
]
DRAW_WORDS = [
    "lucky draw", "luckydraw", "lucky-draw", "sweepstake", "giveaway", "contest",
    "win ", "winner", "prize",
    "ชิงโชค", "ลุ้น", "รางวัล", "แจกฟรี", "ร่วมสนุก", "กิจกรรม",
]
NOISE_URL_WORDS = [
    "login", "signin", "sign-in", "logout", "register", "privacy", "cookie", "careers",
    "my-account", "myaccount", "profile", "manage-booking", "check-in", "checkin",
    "help", "faq", "contact", "sitemap", "terms", "conditions-of-carriage",
]
SKIP_WORDS = [
    "login", "signin", "sign-in", "register", "javascript:", "mailto:", "tel:",
    "privacy", "cookie", "terms-of-use", "careers", "#",
]


# --------------------------------------------------------------------------- HTML

class LinkAndMetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.meta: dict[str, str] = {}
        self.title = ""
        self._href: str | None = None
        self._text: list[str] = []
        self._in_title = False
        self.text_chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "noscript"):
            self._skip_depth += 1
        elif tag == "a" and a.get("href"):
            self._href = a["href"]
            self._text = [a.get("title") or a.get("aria-label") or ""]
        elif tag == "img" and self._href is not None:
            self._text.append(a.get("alt") or "")
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and a.get("content"):
                self.meta.setdefault(key, a["content"])
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag == "a" and self._href is not None:
            self.links.append((self._href, clean(" ".join(self._text))))
            self._href = None
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
        if self._href is not None:
            self._text.append(data)
        if data.strip():
            self.text_chunks.append(data.strip())


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse(html: str) -> LinkAndMetaParser:
    p = LinkAndMetaParser()
    try:
        p.feed(html)
    except Exception:  # broken markup: keep what we got
        pass
    return p


# ------------------------------------------------------------------------ network

_robots: dict[str, urllib.robotparser.RobotFileParser] = {}


def allowed(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    rp = _robots.get(base)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser(base + "/robots.txt")
        try:
            req = urllib.request.Request(base + "/robots.txt", headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                rp.parse(r.read().decode("utf-8", "replace").splitlines())
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                rp.disallow_all = True
            else:
                rp.allow_all = True
        except Exception:
            rp.allow_all = True
        _robots[base] = rp
    return rp.can_fetch(USER_AGENT, url)


_browser = None


def render(url: str) -> str | None:
    """Load a page in headless Chromium (for pages built with JavaScript).

    Returns None when Playwright is not installed.
    """
    global _browser
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    if _browser is None:
        pw = sync_playwright().start()
        _browser = pw.chromium.launch()
    page = _browser.new_page(locale="th-TH")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        page.mouse.wheel(0, 4000)   # trigger lazy-loaded cards
        page.wait_for_timeout(2000)
        return page.content()
    finally:
        page.close()


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept-Language": "th-TH,th;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        charset = r.headers.get_content_charset() or "utf-8"
        return r.read().decode(charset, "replace")


# ------------------------------------------------------------------ classification

def has_word(text: str, words: list[str]) -> bool:
    t = f" {text.lower()} "
    return any(w in t for w in words)


def classify(text: str) -> str:
    return "draw" if has_word(text, DRAW_WORDS) else "fare"


def normalize(url: str) -> str:
    """Same page, same id: https, lower-case host, no fragment, no trailing space."""
    parts = urllib.parse.urlsplit(url.strip())
    scheme = "https" if parts.scheme in ("http", "https") else parts.scheme
    return urllib.parse.urlunsplit((scheme, parts.netloc.lower(), parts.path, parts.query, ""))


def same_site(url: str, base: str) -> bool:
    a = urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.")
    b = urllib.parse.urlsplit(base).netloc.lower().removeprefix("www.")
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def candidate_links(page_url: str, links: list[tuple[str, str]], include: str | None = None) -> list[tuple[str, str]]:
    seen, out = set(), []
    page_norm = page_url.rstrip("/")
    for href, text in links:
        if has_word(href, SKIP_WORDS) and not href.startswith("http"):
            continue
        url = normalize(urllib.parse.urljoin(page_url, href.strip()))
        if not url.startswith("http") or not same_site(url, page_url):
            continue
        if url.rstrip("/") == page_norm or url in seen:
            continue
        if has_word(url.lower(), NOISE_URL_WORDS):
            continue
        if include and not re.search(include, url):
            continue
        haystack = f"{url} {text}"
        if not (has_word(haystack, PROMO_WORDS) or has_word(haystack, DRAW_WORDS)):
            continue
        if len(text) < 4 and urllib.parse.urlsplit(url).path.count("/") < 2:
            continue
        seen.add(url)
        out.append((url, text))
    return out


# ------------------------------------------------------------------------- dates

EN_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
TH_MONTHS = {
    "มกราคม": 1, "ม.ค.": 1, "กุมภาพันธ์": 2, "ก.พ.": 2, "มีนาคม": 3, "มี.ค.": 3,
    "เมษายน": 4, "เม.ย.": 4, "พฤษภาคม": 5, "พ.ค.": 5, "มิถุนายน": 6, "มิ.ย.": 6,
    "กรกฎาคม": 7, "ก.ค.": 7, "สิงหาคม": 8, "ส.ค.": 8, "กันยายน": 9, "ก.ย.": 9,
    "ตุลาคม": 10, "ต.ค.": 10, "พฤศจิกายน": 11, "พ.ย.": 11, "ธันวาคม": 12, "ธ.ค.": 12,
}
_th_pat = "|".join(re.escape(k) for k in sorted(TH_MONTHS, key=len, reverse=True))
RE_TH = re.compile(rf"(\d{{1,2}})\s*({_th_pat})\s*(\d{{2,4}})")
RE_EN_DMY = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})")
RE_EN_MDY = re.compile(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})")
RE_ISO = re.compile(r"(20\d\d)-(\d\d)-(\d\d)")
RE_NUM = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


def _year(y: int) -> int:
    if y < 100:            # 69 -> 2569 (BE) or 26 -> 2026
        y += 2500 if y >= 50 else 2000
    if y > 2400:           # Buddhist Era
        y -= 543
    return y


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(_year(y), m, d)
    except ValueError:
        return None


def find_dates(text: str) -> list[date]:
    out: list[date] = []
    for d, mon, y in RE_TH.findall(text):
        out.append(_mk(int(y), TH_MONTHS[mon], int(d)))
    for d, mon, y in RE_EN_DMY.findall(text):
        if mon[:3].lower() in EN_MONTHS:
            out.append(_mk(int(y), EN_MONTHS[mon[:3].lower()], int(d)))
    for mon, d, y in RE_EN_MDY.findall(text):
        if mon[:3].lower() in EN_MONTHS:
            out.append(_mk(int(y), EN_MONTHS[mon[:3].lower()], int(d)))
    for y, m, d in RE_ISO.findall(text):
        out.append(_mk(int(y), int(m), int(d)))
    for d, m, y in RE_NUM.findall(text):
        out.append(_mk(int(y), int(m), int(d)))
    return [x for x in out if x and 2020 <= x.year <= 2035]


def guess_deadline(text: str, today: date) -> str | None:
    """The latest date mentioned that is not in the past, up to a year ahead."""
    future = [d for d in find_dates(text) if today <= d <= today + timedelta(days=400)]
    return max(future).isoformat() if future else None


# ------------------------------------------------------------------------- feeds

# News about government lottery results also says "ลุ้นรางวัล"; we only want brand campaigns.
FEED_EXCLUDE = ["หวย", "สลากกินแบ่ง", "ล็อตเตอรี่", "เลขเด็ด", "งวดนี้", "ตรวจหวย", "lottery"]


def collect_feed(src: dict, today: date, log) -> list[dict]:
    """News search feed (RSS): headlines about lucky draws from any brand."""
    url = src["url"]
    if not allowed(url):
        log(f"  robots.txt disallows {url}; skipped")
        return []
    root = ET.fromstring(fetch(url).encode("utf-8"))
    max_age = timedelta(days=src.get("max_age_days", 14))
    items = []
    for node in root.iter("item"):
        title = clean(node.findtext("title"))
        link = clean(node.findtext("link"))
        publisher = clean(node.findtext("source"))
        if not title or not link or has_word(title.lower(), FEED_EXCLUDE):
            continue
        try:
            published = parsedate_to_datetime(node.findtext("pubDate")).astimezone(BKK)
        except Exception:
            published = None
        if published and published.date() < today - max_age:
            continue
        if publisher and title.endswith(" - " + publisher):
            title = title[: -len(publisher) - 3]
        items.append({
            "id": item_id(link),
            "airline": src["id"],
            "title": title[:200],
            "description": f"ข่าวจาก {publisher}" if publisher else "",
            "url": link,
            "image": "",
            "type": "draw",
            "deadline": guess_deadline(title, today),
            "published": published.isoformat() if published else None,
            "kind": "feed",
            "source": "auto",
        })
    log(f"  {len(items)} feed items")
    return items


# -------------------------------------------------------------------------- main

def item_id(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def collect_source(src: dict, today: date, log) -> list[dict]:
    url = src["url"]
    if not allowed(url):
        log(f"  robots.txt disallows {url}; skipped")
        return []
    include = src.get("include")
    links, error = [], None
    try:
        page = parse(fetch(url))
        links = candidate_links(url, page.links, include)
    except Exception as e:
        error = e
    if not links:
        html = render(url)
        if html is not None:
            log("  using headless browser")
            page = parse(html)
            links = candidate_links(url, page.links, include)
            error = None
    if error:
        raise error
    if DEBUG:
        for href, text in page.links:
            log(f"    LINK {urllib.parse.urljoin(url, href)} | {clean(text)[:80]}")
    log(f"  {len(links)} candidate links")
    items = []
    for link, text in links[:MAX_DETAIL_PAGES]:
        title, image, desc, body = text, "", "", text
        if allowed(link):
            time.sleep(DELAY_SECONDS)
            try:
                try:
                    d = parse(fetch(link))
                except Exception:
                    html = render(link)
                    if html is None:
                        raise
                    d = parse(html)
                title = clean(d.meta.get("og:title") or d.title) or text
                image = d.meta.get("og:image", "")
                desc = clean(d.meta.get("og:description") or d.meta.get("description", ""))
                body = " ".join([title, desc, " ".join(d.text_chunks)[:20000]])
            except Exception as e:  # keep the link even if the detail page fails
                log(f"    detail failed {link}: {e}")
        if image:
            image = urllib.parse.urljoin(link, image)
        items.append({
            "id": item_id(link),
            "airline": src["id"],
            "title": title[:200] or link,
            "description": desc[:400],
            "url": link,
            "image": image,
            "type": classify(f"{link} {title} {desc}"),
            "deadline": guess_deadline(body, today),
            "source": "auto",
        })
    if src.get("draws_only"):
        items = [i for i in items if i["type"] == "draw"]
    return items


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def merge(previous: list[dict], fresh: list[dict], ok_airlines: set[str], now: str, today: date,
          includes: dict[str, str] | None = None, keep_days: dict[str, int] | None = None) -> list[dict]:
    """Keep first_seen dates; drop items that vanished more than KEEP_MISSING_DAYS ago."""
    prev = {p["id"]: p for p in previous}
    out: dict[str, dict] = {}
    for it in fresh:
        old = prev.get(it["id"], {})
        it["first_seen"] = old.get("first_seen", now)
        it["last_seen"] = now
        out[it["id"]] = it
    def cutoff(airline: str) -> str:
        days = (keep_days or {}).get(airline, KEEP_MISSING_DAYS)
        return (datetime.fromisoformat(now) - timedelta(days=days)).isoformat()
    for pid, p in prev.items():
        if pid in out or p.get("source") != "auto" or has_word(p.get("url", "").lower(), NOISE_URL_WORDS):
            continue
        include = (includes or {}).get(p["airline"])
        if include and not re.search(include, p.get("url", "")):
            continue
        # If the airline failed this run, keep its old items untouched.
        if p["airline"] not in ok_airlines or p.get("last_seen", now) >= cutoff(p["airline"]):
            out[pid] = p
    # Expired deadlines go away.
    return [i for i in out.values() if not i.get("deadline") or i["deadline"] >= today.isoformat()]


def main() -> int:
    log = lambda m: print(m, flush=True)
    sources = load_json(SOURCES, [])
    now_dt = datetime.now(BKK).replace(microsecond=0)
    now, today = now_dt.isoformat(), now_dt.date()
    previous = load_json(OUTPUT, {}).get("items", [])

    fresh, status, ok = [], [], set()
    for src in sources:
        log(f"{src['name']}: {src['url']}")
        try:
            collect = collect_feed if src.get("kind") == "feed" else collect_source
            items = collect(src, today, log)
            fresh.extend(items)
            ok.add(src["id"])
            status.append({"airline": src["id"], "ok": True, "count": len(items)})
        except Exception as e:
            log(f"  failed: {e}")
            status.append({"airline": src["id"], "ok": False, "error": str(e)[:200]})

    includes = {s["id"]: s["include"] for s in sources if s.get("include")}
    keep_days = {s["id"]: s["keep_days"] for s in sources if s.get("keep_days")}
    auto = merge(previous, fresh, ok, now, today, includes, keep_days)
    # The same promotion is often linked twice (image and "read more"): keep one per title.
    seen_titles, unique = set(), []
    for it in sorted(auto, key=lambda i: i.get("first_seen", "")):
        key = ("feed" if it.get("kind") == "feed" else it["airline"], it["title"].strip().lower())
        if key in seen_titles:
            continue
        seen_titles.add(key)
        unique.append(it)
    auto = unique

    manual = []
    for m in load_json(MANUAL, []):
        m = dict(m)
        m.setdefault("id", item_id(m["url"]))
        m.setdefault("type", classify(f"{m['url']} {m.get('title', '')}"))
        m.setdefault("first_seen", m.get("added", now))
        m["source"] = "manual"
        if not m.get("deadline") or m["deadline"] >= today.isoformat():
            manual.append(m)

    by_id = {i["id"]: i for i in auto}
    by_id.update({m["id"]: m for m in manual})   # manual wins over auto
    items = sorted(by_id.values(), key=lambda i: i.get("first_seen", ""), reverse=True)

    if DEBUG:
        log(f"Debug run: {len(items)} items, nothing written")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({
        "updated": now,
        "airlines": {s["id"]: {"name": s["name"], "name_th": s["name_th"], "url": s["url"],
                               "category": s.get("category", "airline")} for s in sources},
        "status": status,
        "items": items,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"Wrote {len(items)} items to {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
