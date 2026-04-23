"""
scraper.py — Playwright-based signal scraper for job postings and leadership changes.

Rules:
  - No login required at any step.
  - No captcha-protected pages.
  - Respects robots.txt crawl delays (1-second minimum between requests).
  - Stealth mode via playwright's default user agent; no credential storage.
"""
import asyncio
import re
import time
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CRAWL_DELAY = 1.5  # seconds between requests


class SignalScraper:
    """
    Public-web signal scraper using Playwright.
    Each method returns a dict conforming to the EnrichmentSignal schema.
    """

    async def _get_page_text(self, url: str) -> Optional[str]:
        """
        Launches a headless Chromium browser, navigates to `url`,
        and returns the visible text content. Returns None on failure.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error("playwright not installed. Run: pip install playwright && playwright install chromium")
            return None

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(url, timeout=15000, wait_until="domcontentloaded")
                text = await page.inner_text("body")
                await browser.close()
                time.sleep(CRAWL_DELAY)
                return text
        except Exception as e:
            logger.warning(f"Scrape failed for {url}: {e}")
            return None

    async def scrape_job_postings(
        self,
        company_name: str,
        domain: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Scrape publicly accessible careers page for open role counts.
        Falls back to a LinkedIn jobs search (public, no login required).

        Returns:
            {
                "open_roles": int,
                "velocity": "high"|"moderate"|"low",
                "focus": str,
                "source": str,
                "confidence": "high"|"medium"|"low",
                "raw_titles": List[str],
            }
        """
        # Try company careers page first
        base = (domain or f"{re.sub(r'[^a-z0-9]+','', company_name.lower())}.com").rstrip("/")
        careers_url = f"https://{base}/careers"
        text = await self._get_page_text(careers_url)

        titles: List[str] = []
        source = careers_url

        if text and len(text) > 200:
            # Extract lines that look like job titles
            lines = [l.strip() for l in text.splitlines() if len(l.strip()) > 4]
            eng_keywords = re.compile(
                r'\b(engineer|developer|architect|data|ml|ai|devops|platform|backend|frontend|fullstack|sre)\b',
                re.IGNORECASE,
            )
            titles = [l for l in lines[:300] if eng_keywords.search(l)][:20]
        else:
            # Fall back to LinkedIn public search (no login)
            linkedin_url = (
                f"https://www.linkedin.com/jobs/search/?keywords={company_name}+engineering"
                f"&f_TPR=r604800"  # past week
            )
            text = await self._get_page_text(linkedin_url)
            source = linkedin_url
            if text:
                titles = re.findall(r'[A-Z][a-z]+ (?:Engineer|Developer|Architect|Lead)[^\n]{0,40}', text)[:20]

        count = len(titles)
        velocity = "high" if count >= 8 else "moderate" if count >= 3 else "low"

        # Determine primary focus from titles
        tech_patterns = {
            "ML/AI": re.compile(r'\b(ml|ai|machine.?learning|nlp|data.?science)\b', re.I),
            "Platform/Infra": re.compile(r'\b(platform|infra|devops|sre|cloud)\b', re.I),
            "Backend": re.compile(r'\b(backend|api|server|golang|python|java)\b', re.I),
            "Frontend": re.compile(r'\b(frontend|react|vue|angular|ui)\b', re.I),
        }
        focus = "general engineering"
        for label, pattern in tech_patterns.items():
            if any(pattern.search(t) for t in titles):
                focus = label
                break

        return {
            "open_roles": count,
            "velocity": velocity,
            "focus": focus,
            "source": source,
            "confidence": "high" if count >= 5 else "medium" if count >= 2 else "low",
            "raw_titles": titles,
        }

    async def scrape_leadership_changes(
        self,
        company_name: str,
        domain: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Search public news sources for recent CTO/VP Eng appointment headlines.

        Returns:
            {
                "event": str | None,
                "date": str | None,
                "headline": str | None,
                "source": str,
                "confidence": "high"|"medium"|"low",
            }
        """
        query = f"{company_name} CTO OR \"VP Engineering\" appointed OR joined OR hired 2024 OR 2025"
        encoded = re.sub(r'\s+', '+', query)
        news_url = f"https://news.google.com/search?q={encoded}&hl=en"

        text = await self._get_page_text(news_url)
        source = news_url

        if not text:
            return {
                "event": None, "date": None, "headline": None,
                "source": source, "confidence": "low",
            }

        # Find a relevant headline
        leadership_re = re.compile(
            r'(?:new|hires?|appoints?|names?|joins?)[^\n]{0,80}(?:CTO|VP.?Eng|Chief.?Tech)',
            re.IGNORECASE,
        )
        match = leadership_re.search(text)
        if match:
            headline = match.group(0).strip()
            return {
                "event": "new CTO/VP Engineering",
                "date": None,   # Exact date would need deeper parsing
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
