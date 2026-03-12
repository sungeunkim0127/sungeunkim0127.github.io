#!/usr/bin/env python3
"""
Scrape Google Scholar for Dr. Sung Eun Kim's publications.

Three-tier scraping with fallbacks:
1. Primary: scholarly library with free proxy rotation
2. Fallback: Semantic Scholar API (free, no key needed)
3. Last resort: SerpAPI (needs SERPAPI_KEY env var)

Merges scraped data into data/publications.json, preserving manual overrides.
"""

import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

# Google Scholar profile ID
SCHOLAR_USER_ID = "KriQPeEAAAAJ"
# Semantic Scholar author ID (can be found at semanticscholar.org)
SEMANTIC_SCHOLAR_AUTHOR_ID = ""  # TODO: Set this up if using Semantic Scholar fallback

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
PUBS_FILE = PROJECT_ROOT / "data" / "publications.json"
OVERRIDES_FILE = PROJECT_ROOT / "data" / "overrides.json"

# Safety threshold: abort if scraped count < this fraction of existing
SAFETY_THRESHOLD = 0.70


def normalize_title(title: str) -> str:
    """Normalize a title for fuzzy matching."""
    title = unicodedata.normalize("NFKD", title)
    title = title.lower().strip()
    title = re.sub(r"[^a-z0-9\s]", "", title)
    title = re.sub(r"\s+", " ", title)
    return title


def make_id(title: str, year: int) -> str:
    """Generate a slug-style ID from title and year."""
    slug = normalize_title(title)
    slug = slug[:60].strip()
    slug = re.sub(r"\s+", "-", slug)
    return f"{slug}-{year}"


def load_existing_pubs() -> list[dict]:
    """Load existing publications from JSON file."""
    if not PUBS_FILE.exists():
        return []
    with open(PUBS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_overrides() -> dict:
    """Load manual overrides config."""
    if not OVERRIDES_FILE.exists():
        return {}
    with open(OVERRIDES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_pubs(pubs: list[dict]) -> None:
    """Save publications to JSON file."""
    PUBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PUBS_FILE, "w", encoding="utf-8") as f:
        json.dump(pubs, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(pubs)} publications to {PUBS_FILE}")


def scrape_scholarly() -> list[dict] | None:
    """Primary: Use scholarly library with free proxy."""
    try:
        from scholarly import scholarly, ProxyGenerator
    except ImportError:
        print("scholarly not installed, skipping primary scraper")
        return None

    print("Attempting scholarly scrape...")
    try:
        # Set up free proxy to avoid rate limiting
        pg = ProxyGenerator()
        success = pg.FreeProxies()
        if success:
            scholarly.use_proxy(pg)
            print("Using free proxy for scholarly")
        else:
            print("Warning: Free proxy setup failed, trying without proxy")
    except Exception as e:
        print(f"Warning: Proxy setup failed ({e}), trying without proxy")

    try:
        author = scholarly.search_author_id(SCHOLAR_USER_ID)
        author = scholarly.fill(author, sections=["publications"])
    except Exception as e:
        print(f"scholarly scrape failed: {e}")
        return None

    pubs = []
    for pub in author.get("publications", []):
        try:
            # Fill individual publication for full details
            filled = scholarly.fill(pub)
            bib = filled.get("bib", {})

            title = bib.get("title", "")
            if not title:
                continue

            year = int(bib.get("pub_year", 0))
            authors = bib.get("author", "")
            venue = bib.get("venue", "") or bib.get("journal", "")
            citation_count = filled.get("num_citations", 0)
            scholar_cid = filled.get("author_pub_id", "")

            pubs.append({
                "title": title,
                "authors": authors,
                "venue": venue,
                "year": year,
                "citation_count": citation_count,
                "scholar_cid": scholar_cid,
            })

            # Be polite to Google Scholar
            time.sleep(1)

        except Exception as e:
            print(f"Warning: Failed to process pub '{pub.get('bib', {}).get('title', '?')}': {e}")
            continue

    if pubs:
        print(f"scholarly scraped {len(pubs)} publications")
        return pubs
    return None


def scrape_semantic_scholar() -> list[dict] | None:
    """Fallback: Use Semantic Scholar API (free, no key needed)."""
    if not SEMANTIC_SCHOLAR_AUTHOR_ID:
        print("Semantic Scholar author ID not configured, skipping")
        return None

    import requests

    print("Attempting Semantic Scholar API scrape...")
    url = f"https://api.semanticscholar.org/graph/v1/author/{SEMANTIC_SCHOLAR_AUTHOR_ID}/papers"
    params = {
        "fields": "title,authors,venue,year,citationCount,externalIds",
        "limit": 500,
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"Semantic Scholar API failed: {e}")
        return None

    pubs = []
    for paper in data.get("data", []):
        title = paper.get("title", "")
        if not title:
            continue

        authors = ", ".join(a.get("name", "") for a in paper.get("authors", []))
        venue = paper.get("venue", "") or ""
        year = paper.get("year") or 0
        citation_count = paper.get("citationCount", 0)

        pubs.append({
            "title": title,
            "authors": authors,
            "venue": venue,
            "year": year,
            "citation_count": citation_count,
            "scholar_cid": "",
        })

    if pubs:
        print(f"Semantic Scholar scraped {len(pubs)} publications")
        return pubs
    return None


def scrape_serpapi() -> list[dict] | None:
    """Last resort: Use SerpAPI (needs SERPAPI_KEY env var)."""
    api_key = os.environ.get("SERPAPI_KEY", "")
    if not api_key:
        print("SERPAPI_KEY not set, skipping SerpAPI scraper")
        return None

    import requests

    print("Attempting SerpAPI scrape...")
    pubs = []
    start = 0

    while True:
        params = {
            "engine": "google_scholar_author",
            "author_id": SCHOLAR_USER_ID,
            "api_key": api_key,
            "start": start,
            "num": 100,
        }

        try:
            resp = requests.get("https://serpapi.com/search", params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"SerpAPI request failed: {e}")
            break

        articles = data.get("articles", [])
        if not articles:
            break

        for article in articles:
            title = article.get("title", "")
            if not title:
                continue

            authors = article.get("authors", "")
            year_str = article.get("year", "")
            year = int(year_str) if year_str and year_str.isdigit() else 0
            citation_count = article.get("cited_by", {}).get("value", 0)
            venue = article.get("publication", "")

            pubs.append({
                "title": title,
                "authors": authors,
                "venue": venue,
                "year": year,
                "citation_count": citation_count,
                "scholar_cid": article.get("citation_id", ""),
            })

        start += len(articles)
        if start >= data.get("search_information", {}).get("total_results", 0):
            break
        time.sleep(1)

    if pubs:
        print(f"SerpAPI scraped {len(pubs)} publications")
        return pubs
    return None


def merge_pubs(existing: list[dict], scraped: list[dict]) -> list[dict]:
    """
    Merge scraped publications into existing list.
    - Match by normalized title
    - Update citation counts for existing pubs
    - Add genuinely new pubs (default: not first-author)
    """
    # Build lookup by normalized title
    existing_by_title: dict[str, dict] = {}
    for pub in existing:
        key = normalize_title(pub["title"])
        existing_by_title[key] = pub

    updated = 0
    added = 0

    for scraped_pub in scraped:
        key = normalize_title(scraped_pub["title"])

        if key in existing_by_title:
            # Update citation count if scraped value is higher
            epub = existing_by_title[key]
            if scraped_pub.get("citation_count", 0) > epub.get("citation_count", 0):
                epub["citation_count"] = scraped_pub["citation_count"]
                updated += 1
            # Update scholar_cid if we didn't have one
            if scraped_pub.get("scholar_cid") and not epub.get("scholar_cid"):
                epub["scholar_cid"] = scraped_pub["scholar_cid"]
        else:
            # New publication
            year = scraped_pub.get("year", 0)
            new_pub = {
                "id": make_id(scraped_pub["title"], year),
                "title": scraped_pub["title"],
                "authors": scraped_pub.get("authors", ""),
                "venue": scraped_pub.get("venue", ""),
                "year": year,
                "is_first_author": False,
                "author_role": "",
                "citation_count": scraped_pub.get("citation_count", 0),
                "scholar_cid": scraped_pub.get("scholar_cid", ""),
            }
            existing.append(new_pub)
            existing_by_title[key] = new_pub
            added += 1

    print(f"Merge result: {updated} updated, {added} new, {len(existing)} total")
    return existing


def main() -> int:
    print("=" * 60)
    print("Scholar Scraper for Sung Eun Kim")
    print("=" * 60)

    existing = load_existing_pubs()
    existing_count = len(existing)
    print(f"Existing publications: {existing_count}")

    # Try scrapers in order
    scraped = None
    for scraper_fn in [scrape_scholarly, scrape_semantic_scholar, scrape_serpapi]:
        scraped = scraper_fn()
        if scraped:
            break

    if not scraped:
        print("WARNING: All scrapers failed. No changes made.")
        return 1

    # Safety check: abort if scraped count is suspiciously low
    if existing_count > 0 and len(scraped) < existing_count * SAFETY_THRESHOLD:
        print(
            f"SAFETY ABORT: Scraped only {len(scraped)} pubs, "
            f"but expected at least {int(existing_count * SAFETY_THRESHOLD)} "
            f"(70% of {existing_count}). Likely a partial scrape."
        )
        return 1

    # Merge and save
    merged = merge_pubs(existing, scraped)

    # Sort by year descending, then title
    merged.sort(key=lambda p: (-p.get("year", 0), p.get("title", "")))

    save_pubs(merged)
    print("Scrape completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
