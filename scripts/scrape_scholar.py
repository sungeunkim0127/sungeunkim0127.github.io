#!/usr/bin/env python3
"""
Scrape Google Scholar for Dr. Sung Eun Kim's publications.

Four-tier scraping with fallbacks:
1. Primary: read the public profile listing page directly (one request per
   100 papers)
2. Fallback: scholarly library with free proxy rotation
3. Fallback: Semantic Scholar API (free, no key needed)
4. Last resort: SerpAPI (needs SERPAPI_KEY env var)

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
META_FILE = PROJECT_ROOT / "data" / "scholar_meta.json"

# Profile-level metadata captured during scraping (e.g. true total citations)
PROFILE: dict = {}

# Safety threshold: abort if scraped count < this fraction of existing
SAFETY_THRESHOLD = 0.70


def normalize_title(title: str) -> str:
    """Normalize a title for fuzzy matching."""
    title = unicodedata.normalize("NFKD", title)
    title = title.lower().strip()
    title = re.sub(r"[^a-z0-9\s]", "", title)
    title = re.sub(r"\s+", " ", title)
    return title


# Titles are compared on this many leading alphanumeric characters when they
# don't match exactly.
MATCH_PREFIX = 45
# Profile owner as first author, in any of the forms Scholar and the curated list use.
FIRST_AUTHOR_RE = re.compile(r"^\s*(SE Kim|Kim SE|Sung Eun Kim|Sung-Eun Kim)\b", re.IGNORECASE)


def match_key(title: str) -> str:
    """Title reduced to lowercase alphanumerics, so spacing, hyphenation, and
    punctuation differences (and Scholar's trailing ellipsis) don't split a paper."""
    title = unicodedata.normalize("NFKD", title or "").lower()
    return re.sub(r"[^a-z0-9]", "", title)


def is_preprint_venue(venue: str) -> bool:
    v = (venue or "").lower().strip()
    return not v or any(k in v for k in ("arxiv", "medrxiv", "biorxiv", "preprint", "under review"))


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


SCHOLAR_URL = "https://scholar.google.com/citations"
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def clean_listing_venue(venue: str) -> str:
    """Tidy a venue string from the profile listing.

    The listing appends volume/pages and cuts long venues off with an ellipsis.
    Drop trailing page numbers; where the name itself was cut, end on a whole
    word and keep the ellipsis so the truncation stays visible.
    """
    v = (venue or "").strip()
    truncated = v.endswith("…") or v.endswith("...")
    if truncated:
        v = v.rstrip("….").rstrip()
        head, sep, tail = v.rpartition(", ")
        if sep and re.fullmatch(r"[\d\s\-–]*", tail):
            v = head  # only the page numbers were cut
        elif " " in v:
            v = v.rsplit(" ", 1)[0].rstrip(" ,:;") + " …"
    # ", 1-14" is a page range; "10-2024-0,114,724" (no space) is part of a number.
    return re.sub(r",\s+\d[\d\s\-–]*$", "", v).strip()


def scrape_profile_page() -> list[dict] | None:
    """Primary: parse the public profile listing directly.

    The listing gives title, authors, venue, year, and citation count for every
    paper in one request per 100 papers, plus the profile total. Long titles,
    author lists, and venues come back truncated; per-paper detail pages sit
    behind a browser check, so they are not fetched.
    """
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError as e:
        print(f"Profile-page scraper unavailable ({e}), skipping")
        return None

    print("Attempting direct profile-page scrape...")
    session = requests.Session()
    pubs = []
    cstart = 0
    page_size = 100

    while True:
        try:
            resp = session.get(
                SCHOLAR_URL,
                params={"user": SCHOLAR_USER_ID, "hl": "en", "cstart": cstart, "pagesize": page_size},
                headers=BROWSER_HEADERS,
                timeout=30,
            )
            resp.raise_for_status()
        except Exception as e:
            print(f"Profile-page request failed: {e}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")

        # Headline total: persist it the moment it is captured.
        if cstart == 0:
            cell = soup.select_one("td.gsc_rsb_std")
            try:
                total = int(cell.get_text(strip=True)) if cell else 0
            except ValueError:
                total = 0
            if total:
                PROFILE["total_citations"] = total
                print(f"Profile total citations: {total}")
                write_meta()

        rows = [r for r in soup.select("tr.gsc_a_tr") if r.select_one("a.gsc_a_at")]
        if not rows:
            if cstart == 0:
                print("Profile page returned no publication rows (blocked or layout changed)")
            break

        for row in rows:
            link = row.select_one("a.gsc_a_at")
            title = link.get_text(strip=True)
            if not title:
                continue
            m = re.search(r"citation_for_view=([^&]+)", link.get("href", ""))
            cid = m.group(1) if m else ""
            grays = row.select("div.gs_gray")
            authors = grays[0].get_text(strip=True) if grays else ""
            venue = ""
            if len(grays) > 1:
                for span in grays[1].select("span.gs_oph"):
                    span.extract()  # drop the trailing ", <year>"
                venue = clean_listing_venue(grays[1].get_text(strip=True))
            cites_cell = row.select_one("td.gsc_a_c")
            year_cell = row.select_one("td.gsc_a_y")
            cites_txt = cites_cell.get_text(strip=True).rstrip("*") if cites_cell else ""
            year_txt = year_cell.get_text(strip=True) if year_cell else ""
            pubs.append({
                "title": title,
                "authors": authors,
                "venue": venue,
                "year": int(year_txt) if year_txt.isdigit() else 0,
                "citation_count": int(cites_txt) if cites_txt.isdigit() else 0,
                "scholar_cid": cid,
            })

        if len(rows) < page_size:
            break
        cstart += page_size
        time.sleep(2)

    if pubs:
        print(f"Profile page scraped {len(pubs)} publications")
        return pubs
    return None


def scrape_scholarly() -> list[dict] | None:
    """Fallback: Use scholarly library with free proxy."""
    try:
        from scholarly import scholarly, ProxyGenerator
    except Exception as e:
        # Not only "not installed": a broken transitive dependency lands here too.
        print(f"scholarly import failed ({type(e).__name__}: {e}), skipping")
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
        author = scholarly.fill(author, sections=["basics", "indices", "publications"])
    except Exception as e:
        print(f"scholarly scrape failed: {e}")
        return None

    # Capture the profile's true total citation count and persist it IMMEDIATELY,
    # before the slow/blockable per-paper loop below — so a mid-loop failure or
    # timeout can never discard a freshly-scraped headline number.
    try:
        total = int(author.get("citedby", 0) or 0)
        if total:
            PROFILE["total_citations"] = total
            print(f"Profile total citations: {total}")
            write_meta()
    except Exception:
        pass

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

        # Capture profile total citations from the first page
        if start == 0:
            try:
                for row in data.get("cited_by", {}).get("table", []):
                    if "citations" in row:
                        total = int(row["citations"].get("all", 0) or 0)
                        if total:
                            PROFILE["total_citations"] = total
                            print(f"Profile total citations: {total}")
                        break
            except Exception:
                pass

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
    - Match by Scholar id, then by title (exact, then by leading characters,
      since Scholar truncates long titles and varies hyphenation)
    - Update citation counts for existing pubs
    - Move a preprint to its published venue/year once Scholar lists one
    - Add genuinely new pubs
    """
    by_cid: dict[str, dict] = {}
    by_title: dict[str, dict] = {}
    by_prefix: dict[str, dict] = {}
    # Hand-curated records (short ids, added first) win ties over auto-scraped ones.
    for pub in sorted(existing, key=lambda p: len(p.get("id", ""))):
        key = match_key(pub["title"])
        by_title.setdefault(key, pub)
        by_prefix.setdefault(key[:MATCH_PREFIX], pub)
    # Scholar ids are the fallback, for papers whose title changed on Scholar.
    for pub in existing:
        if pub.get("scholar_cid"):
            by_cid.setdefault(pub["scholar_cid"], pub)

    updated = 0
    published = 0
    added = 0

    for scraped_pub in scraped:
        key = match_key(scraped_pub["title"])
        epub = by_title.get(key) or by_prefix.get(key[:MATCH_PREFIX])
        if epub is None:
            epub = by_cid.get(scraped_pub.get("scholar_cid") or "")

        if epub is not None:
            # Update citation count if scraped value is higher
            if scraped_pub.get("citation_count", 0) > epub.get("citation_count", 0):
                epub["citation_count"] = scraped_pub["citation_count"]
                updated += 1
            # Update scholar_cid if we didn't have one
            if scraped_pub.get("scholar_cid") and not epub.get("scholar_cid"):
                epub["scholar_cid"] = scraped_pub["scholar_cid"]
            # Preprint on file, published venue on Scholar: record the publication.
            if is_preprint_venue(epub.get("venue", "")) and not is_preprint_venue(scraped_pub.get("venue", "")):
                print(f"Now published: {epub['title'][:60]!r} -> {scraped_pub['venue']}")
                epub["venue"] = scraped_pub["venue"]
                if scraped_pub.get("year"):
                    epub["year"] = scraped_pub["year"]
                published += 1
            continue

        # New publication
        year = scraped_pub.get("year", 0)
        if not year:
            # Undated rows are usually Scholar's stray duplicate of a dated one.
            print(f"Skipping undated new entry: {scraped_pub['title'][:60]!r}")
            continue
        authors = scraped_pub.get("authors", "")
        is_first = bool(FIRST_AUTHOR_RE.match(authors))
        new_pub = {
            "id": make_id(scraped_pub["title"], year),
            "title": scraped_pub["title"],
            "authors": authors,
            "venue": scraped_pub.get("venue", ""),
            "year": year,
            "is_first_author": is_first,
            "author_role": "first" if is_first else "",
            "citation_count": scraped_pub.get("citation_count", 0),
            "scholar_cid": scraped_pub.get("scholar_cid", ""),
        }
        if scraped_pub.get("url"):
            new_pub["url"] = scraped_pub["url"]
        print(f"New publication: {new_pub['title'][:70]!r} ({year})")
        existing.append(new_pub)
        by_title[key] = new_pub
        by_prefix.setdefault(key[:MATCH_PREFIX], new_pub)
        added += 1

    print(f"Merge result: {updated} citation updates, {published} newly published, {added} new, {len(existing)} total")
    return existing


def write_meta() -> bool:
    """Persist the profile-level total citation count, preserving the prior value
    if this run didn't capture one. Returns True if a non-zero total is on disk.

    This is decoupled from the per-paper crawl on purpose: the headline number is
    cheap to fetch (one request for the author profile), while the per-paper crawl
    is slow and frequently blocked. Persisting here, the moment a total is captured,
    means a blocked crawl can never discard a freshly-scraped total.
    """
    from datetime import date

    prev = {}
    if META_FILE.exists():
        try:
            with open(META_FILE, "r", encoding="utf-8") as f:
                prev = json.load(f)
        except Exception:
            prev = {}

    total = int(PROFILE.get("total_citations", 0) or 0)
    fresh = bool(total)
    if not total:
        # Fall back to whatever was last persisted, so we never zero it out.
        total = int(prev.get("total_citations", 0) or 0)
    if not total:
        return False

    # last_checked moves only when Scholar actually answered, so the data file
    # changes on every successful run and its git history dates the last refresh
    # (the workflow's stale-data check relies on that).
    meta = {"total_citations": total, "scholar_id": SCHOLAR_USER_ID}
    checked = date.today().isoformat() if fresh else prev.get("last_checked")
    if checked:
        meta["last_checked"] = checked

    META_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"Wrote profile metadata: total_citations={total}")
    return True


def main() -> int:
    print("=" * 60)
    print("Scholar Scraper for Sung Eun Kim")
    print("=" * 60)

    existing = load_existing_pubs()
    existing_count = len(existing)
    print(f"Existing publications: {existing_count}")

    # Try scrapers in order. Persist the profile total the moment any tier
    # captures it — independent of whether the per-paper crawl succeeds.
    scraped = None
    for scraper_fn in [scrape_profile_page, scrape_scholarly, scrape_semantic_scholar, scrape_serpapi]:
        scraped = scraper_fn()
        if PROFILE.get("total_citations"):
            write_meta()
        if scraped:
            break

    got_total = bool(PROFILE.get("total_citations"))

    # Publications crawl is best-effort; the headline total is the priority.
    if scraped and not (existing_count > 0 and len(scraped) < existing_count * SAFETY_THRESHOLD):
        merged = merge_pubs(existing, scraped)
        merged.sort(key=lambda p: (-p.get("year", 0), p.get("title", "")))
        save_pubs(merged)
    elif scraped:
        print(
            f"SAFETY ABORT (pubs): scraped only {len(scraped)}, expected >= "
            f"{int(existing_count * SAFETY_THRESHOLD)}. Keeping existing publications."
        )
    else:
        print("Note: per-paper crawl returned nothing this run.")

    # Outcome: success if we refreshed the headline total OR the pub list.
    if got_total:
        print("Scrape completed: profile total refreshed.")
        return 0
    if scraped:
        print("Scrape completed: publications refreshed (no profile total this run).")
        # Make sure index.html can still render a citation number from existing data.
        write_meta()
        return 0

    # Nothing captured. Make the failure loud in CI, but don't wipe anything.
    print("::warning::Scholar scrape captured neither profile total nor publications this run; citation number left unchanged.")
    print("WARNING: All scrapers failed. No changes made.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
