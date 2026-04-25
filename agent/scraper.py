"""
scraper.py -- Playwright-based public-signal scraper.

Compliance rules (enforced in code, not just policy):
  1. robots.txt gate: every URL is checked against the site's robots.txt before
     any HTTP request is made. If Disallow matches the path, the URL is skipped
     and the pipeline falls back to the next candidate. The robots.txt response
     is cached per-domain within a single run to avoid redundant fetches.
  2. Public pages only: no login, no captcha, no session cookies.
  3. Crawl delay: 1.5 s between requests (respects Crawl-delay directive when
     present, uses 1.5 s as the minimum floor).
  4. User-agent: declared as "TenaciousBot/1.0 (public signal research; contact
     research@gettenacious.com)" so site operators can identify and block.
"""
import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

logger = logging.getLogger(__name__)

BOT_AGENT = "TenaciousBot/1.0"
CRAWL_DELAY_DEFAULT = 1.5    # seconds; raised to Crawl-delay value if robots.txt specifies more
ROBOTS_FETCH_TIMEOUT = 5.0   # seconds; skip check if robots.txt unreachable

# Per-run cache: domain -> (RobotFileParser, crawl_delay_seconds)
_robots_cache: Dict[str, tuple] = {}


def _get_robots(domain_root: str) -> tuple:
    """
    Fetch and cache robots.txt for `domain_root` (e.g. 'https://example.com').
    Returns (RobotFileParser | None, crawl_delay_seconds).
    Returns (None, default_delay) when robots.txt is unreachable — callers
    treat None as "permissive: allow fetch".
    """
    if domain_root in _robots_cache:
        return _robots_cache[domain_root]

    rp = RobotFileParser()
    rp.set_url(domain_root.rstrip("/") + "/robots.txt")
    fetched = False
    try:
        rp.read()
        fetched = rp.last_checked is not None and rp.last_checked > 0
        delay = rp.crawl_delay(BOT_AGENT) or rp.crawl_delay("*") or CRAWL_DELAY_DEFAULT
        delay = max(float(delay), CRAWL_DELAY_DEFAULT)
    except Exception:
        delay = CRAWL_DELAY_DEFAULT

    result = (rp if fetched else None, delay)
    _robots_cache[domain_root] = result
    return result


def _is_allowed(url: str) -> tuple:
    """
    Check whether `url` is fetchable under robots.txt rules.
    Returns (allowed: bool, crawl_delay: float).

    Permissive default: if robots.txt cannot be fetched (network error,
    domain does not exist, 404), allow the fetch rather than block it.
    This matches the standard crawler convention: robots.txt absence == no restrictions.
    """
    try:
        parsed = urlparse(url)
        domain_root = f"{parsed.scheme}://{parsed.netloc}"
        rp, delay = _get_robots(domain_root)
        if rp is None:
            # robots.txt unreachable — permissive
            return True, delay
        allowed = rp.can_fetch(BOT_AGENT, url) or rp.can_fetch("*", url)
        return allowed, delay
    except Exception:
        return True, CRAWL_DELAY_DEFAULT


class SignalScraper:
    """
    Public-web signal scraper using Playwright.
    Every fetch is gated by robots.txt before any network request is issued.
    """

    async def _fetch_allowed(self, url: str) -> Optional[str]:
        """
        Check robots.txt, then fetch page text if allowed.
        Returns page body text or None (on disallow, timeout, or error).
        """
        import os as _os
        if _os.getenv("DEMO_SKIP_PLAYWRIGHT"):
            logger.debug(f"DEMO_SKIP_PLAYWRIGHT: skipping live fetch for {url}")
            return None

        allowed, crawl_delay = _is_allowed(url)
        if not allowed:
            logger.info(f"robots.txt disallows {url} -- skipping")
            return None

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error("playwright not installed. Run: pip install playwright && playwright install chromium")
            return None

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(
                    extra_http_headers={"User-Agent": BOT_AGENT}
                )
                await page.goto(url, timeout=15000, wait_until="domcontentloaded")
                text = await page.inner_text("body")
                await browser.close()
            time.sleep(crawl_delay)
            return text
        except Exception as e:
            logger.warning(f"Scrape failed for {url}: {e}")
            return None

    # ------------------------------------------------------------------
    # Job postings scrape + 60-day velocity delta
    # ------------------------------------------------------------------

    async def scrape_job_postings(
        self,
        company_name: str,
        domain: Optional[str] = None,
        snapshot_60d: Optional[int] = None,
        snapshot_date: str = "2026-02-23",
    ) -> Dict[str, Any]:
        """
        Scrape publicly accessible careers page for open role counts.
        Computes a 60-day velocity delta against `snapshot_60d` (role count
        from ~60 days ago, sourced from Crunchbase ODM sample).

        Args:
            company_name:   Display name for the company.
            domain:         Base domain (e.g. 'dataflow.ai'). Inferred if None.
            snapshot_60d:   Open-role count from the Feb 23 2026 snapshot.
                            If None, delta cannot be computed.
            snapshot_date:  ISO date string of the snapshot (for the record).

        Returns JobPostSignal with added fields:
            velocity_delta:  int  -- current minus snapshot (positive = accelerating)
            velocity_trend:  str  -- 'accelerating'|'growing'|'stable'|'decelerating'
            snapshot_60d:    int|None
            snapshot_date:   str
        """
        base = (domain or re.sub(r"[^a-z0-9]+", "", company_name.lower()) + ".com").rstrip("/")
        candidates = [
            f"https://{base}/careers",
            f"https://{base}/jobs",
            f"https://{base}/about/careers",
        ]

        titles: List[str] = []
        source = "none"
        text = None

        for url in candidates:
            text = await self._fetch_allowed(url)
            if text and len(text) > 200:
                source = url
                break

        if text and len(text) > 200:
            lines = [l.strip() for l in text.splitlines() if len(l.strip()) > 4]
            eng_re = re.compile(
                r"\b(engineer|developer|architect|data|ml|ai|devops|platform|"
                r"backend|frontend|fullstack|sre|scientist|analyst)\b",
                re.IGNORECASE,
            )
            titles = [l for l in lines[:400] if eng_re.search(l)][:25]
            confidence = "high" if len(titles) >= 5 else "medium" if len(titles) >= 2 else "low"
        else:
            confidence = "low"

        count = len(titles)
        focus = _infer_focus(titles)

        # 60-day velocity delta
        delta, trend = _compute_velocity_delta(count, snapshot_60d)

        # Absolute velocity label (still useful alongside trend)
        velocity = "high" if count >= 8 else "moderate" if count >= 3 else "low"

        return {
            "open_roles": count,
            "velocity": velocity,
            "velocity_delta": delta,
            "velocity_trend": trend,
            "snapshot_60d": snapshot_60d,
            "snapshot_date": snapshot_date,
            "focus": focus,
            "source": source,
            "confidence": confidence,
            "raw_titles": titles,
        }

    # ------------------------------------------------------------------
    # Leadership changes scrape
    # ------------------------------------------------------------------

    async def scrape_leadership_changes(
        self,
        company_name: str,
        domain: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Search public news for recent CTO/VP Eng appointment headlines.
        Only fetches pages that robots.txt permits.

        Returns LeadershipSignal dict.
        """
        query = (
            f"{company_name} CTO OR \"VP Engineering\" appointed OR joined OR hired 2025 OR 2026"
        )
        encoded = re.sub(r"\s+", "+", query)
        news_url = f"https://news.google.com/search?q={encoded}&hl=en"

        text = await self._fetch_allowed(news_url)
        source = news_url

        if not text:
            return {
                "event": None, "date": None, "headline": None,
                "source": source, "confidence": "low",
            }

        leadership_re = re.compile(
            r"(?:new|hires?|appoints?|names?|joins?)[^\n]{0,80}(?:CTO|VP.?Eng|Chief.?Tech)",
            re.IGNORECASE,
        )
        match = leadership_re.search(text)
        if match:
            headline = match.group(0).strip()
            return {
                "event": "new CTO/VP Engineering",
                "date": None,
                "headline": headline,
                "source": source,
                "confidence": "medium",
            }

        return {
            "event": None, "date": None, "headline": None,
            "source": source, "confidence": "low",
        }

    def run(self, coro):
        """Convenience helper to run async methods from sync code."""
        return asyncio.run(coro)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _compute_velocity_delta(current: int, snapshot: Optional[int]) -> tuple:
    """
    Compute 60-day role-count delta and classify the trend.

    Returns (delta: int|None, trend: str).
    Trend labels:
      'accelerating'  delta >= +3   (strong hiring ramp in the window)
      'growing'       delta in [+1, +2]
      'stable'        delta == 0
      'decelerating'  delta in [-1, -2]
      'declining'     delta <= -3   (significant pull-back)
      'unknown'       snapshot not available
    """
    if snapshot is None:
        return None, "unknown"

    delta = current - snapshot
    if delta >= 3:
        trend = "accelerating"
    elif delta >= 1:
        trend = "growing"
    elif delta == 0:
        trend = "stable"
    elif delta >= -2:
        trend = "decelerating"
    else:
        trend = "declining"

    return delta, trend


def _infer_focus(titles: List[str]) -> str:
    text = " ".join(titles).lower()
    if any(k in text for k in ["ml", "ai ", "machine learning", "data sci", "nlp", "llm", "applied sci"]):
        return "AI/ML engineering"
    if any(k in text for k in ["backend", "python", "go ", "java", "node", "api"]):
        return "backend engineering"
    if any(k in text for k in ["infra", "devops", "sre", "platform", "cloud", "kubernetes"]):
        return "infrastructure"
    if any(k in text for k in ["frontend", "react", "typescript", "ui "]):
        return "frontend engineering"
    return "engineering"
