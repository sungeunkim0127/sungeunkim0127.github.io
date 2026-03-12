#!/usr/bin/env python3
"""
Generate the year-grouped publication list HTML from publications.json.

Reads data/publications.json + data/overrides.json, generates HTML matching
the existing CSS classes, and replaces content between AUTO-GENERATED markers
in index.html.
"""

import json
import re
import sys
from collections import defaultdict
from html import escape
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
PUBS_FILE = PROJECT_ROOT / "data" / "publications.json"
OVERRIDES_FILE = PROJECT_ROOT / "data" / "overrides.json"
INDEX_FILE = PROJECT_ROOT / "index.html"

START_MARKER = "<!-- AUTO-GENERATED-PUBS-START -->"
END_MARKER = "<!-- AUTO-GENERATED-PUBS-END -->"


def load_json(path: Path) -> dict | list:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def highlight_author(authors_str: str, highlight_name: str) -> str:
    """Wrap the highlighted author name with <strong> tags."""
    # Escape HTML first
    safe = escape(authors_str)
    hl = escape(highlight_name)
    # Replace the author name with strong-wrapped version
    # Handle "Kim SE" appearing with or without asterisk markers
    # Pattern: match "Kim SE" possibly preceded by space and followed by *
    safe = safe.replace(hl, f"<strong>{hl}</strong>")
    return safe


def format_venue(venue: str) -> str:
    """Wrap venue name in <em> tags if present."""
    if not venue:
        return ""
    return f"<em>{escape(venue)}</em>"


def generate_pub_item(pub: dict, highlight_name: str) -> str:
    """Generate HTML for a single publication list item."""
    is_first = pub.get("is_first_author", False)
    css_class = "pub-list-item first-author-item" if is_first else "pub-list-item"

    title = escape(pub.get("title", ""))
    authors_raw = pub.get("authors", "")
    venue = pub.get("venue", "")
    url = pub.get("url", "")

    # Wrap title in a link if URL is available
    if url:
        title_html = f'<a href="{escape(url)}" target="_blank" rel="noopener">{title}</a>'
    else:
        title_html = title

    # Apply overrides for author display
    authors_html = highlight_author(authors_raw, highlight_name)

    # Build the pub-meta line: "Authors. Venue"
    meta_parts = []
    if authors_html:
        meta_parts.append(authors_html)
    if venue:
        meta_parts.append(format_venue(venue))

    meta_html = ". ".join(meta_parts) if meta_parts else ""

    return (
        f'                        <div class="{css_class}">\n'
        f"                            <h4>{title_html}</h4>\n"
        f'                            <p class="pub-meta">{meta_html}</p>\n'
        f"                        </div>"
    )


def generate_year_group(year_label: str, pubs: list[dict], highlight_name: str, year_id: str) -> str:
    """Generate HTML for a year group."""
    count = len(pubs)
    papers_word = "paper" if count == 1 else "papers"

    items_html = "\n".join(generate_pub_item(p, highlight_name) for p in pubs)

    return (
        f'                <div class="pub-year-group" id="{year_id}">\n'
        f'                    <div class="pub-year-label">{year_label} <span class="year-count">{count} {papers_word}</span></div>\n'
        f'                    <div class="pub-list-items">\n'
        f"{items_html}\n"
        f"                    </div>\n"
        f"                </div>"
    )


def apply_overrides(pubs: list[dict], overrides: dict) -> list[dict]:
    """Apply per-publication overrides from overrides.json."""
    pub_overrides = overrides.get("publication_overrides", {})
    for pub in pubs:
        pub_id = pub.get("id", "")
        if pub_id in pub_overrides:
            for key, value in pub_overrides[pub_id].items():
                pub[key] = value
    return pubs


def generate_all_pubs_html(pubs: list[dict], overrides: dict) -> str:
    """Generate the complete year-grouped publications HTML."""
    highlight_name = overrides.get("author_highlight_name", "Kim SE")
    cutoff_year = overrides.get("show_more_cutoff_year", 2025)

    # Apply overrides
    pubs = apply_overrides(pubs, overrides)

    # Group by year
    by_year: dict[int, list[dict]] = defaultdict(list)
    for pub in pubs:
        by_year[pub.get("year", 0)].append(pub)

    # Sort years descending
    sorted_years = sorted(by_year.keys(), reverse=True)

    # Split into visible (>= cutoff) and hidden (< cutoff)
    visible_years = [y for y in sorted_years if y >= cutoff_year]
    hidden_years = [y for y in sorted_years if y < cutoff_year]

    html_parts = []

    # Visible year groups
    for year in visible_years:
        year_pubs = by_year[year]
        year_id = f"pubs-{year}"
        html_parts.append(generate_year_group(str(year), year_pubs, highlight_name, year_id))

    # Hidden year groups (inside earlierPubs div)
    if hidden_years:
        html_parts.append("")
        html_parts.append('                <!-- Earlier years - hidden by default -->')
        html_parts.append('                <div id="earlierPubs" style="display: none;">')
        html_parts.append("")

        for year in hidden_years:
            year_pubs = by_year[year]
            if year <= min(hidden_years):
                # Last group: combine with "& Earlier" label
                year_label = f"{year} & Earlier" if year > 0 else "Other"
            else:
                year_label = str(year)
            year_id = f"pubs-{year}"
            html_parts.append(generate_year_group(year_label, year_pubs, highlight_name, year_id))
            html_parts.append("")

        html_parts.append("                </div><!-- end earlierPubs -->")

    # Determine show-more button text
    if hidden_years:
        earliest_visible = min(visible_years) if visible_years else "earlier"
        btn_text = f"Show {int(earliest_visible) - 1} & Earlier Publications"
    else:
        btn_text = "Show Earlier Publications"

    html_parts.append("")
    html_parts.append(
        f'                <button class="show-more-btn" id="showMoreBtn" aria-expanded="false" '
        f'aria-controls="earlierPubs" onclick="togglePublications()">\n'
        f'                    <span id="showMoreText">{btn_text}</span>\n'
        f"                </button>"
    )

    return "\n".join(html_parts)


def inject_into_html(html_content: str, generated_html: str) -> str:
    """Replace content between AUTO-GENERATED markers."""
    if START_MARKER not in html_content:
        raise ValueError(f"Start marker not found in index.html: {START_MARKER}")
    if END_MARKER not in html_content:
        raise ValueError(f"End marker not found in index.html: {END_MARKER}")

    pattern = re.compile(
        re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER),
        re.DOTALL,
    )

    replacement = f"{START_MARKER}\n{generated_html}\n                {END_MARKER}"
    new_html = pattern.sub(replacement, html_content)

    return new_html


def validate_html(html_content: str) -> bool:
    """Basic validation of the generated HTML."""
    checks = [
        (START_MARKER in html_content, "Start marker missing"),
        (END_MARKER in html_content, "End marker missing"),
        ("</html>" in html_content, "</html> tag missing"),
        (html_content.count("<section") == html_content.count("</section>"), "Unbalanced <section> tags"),
    ]

    all_ok = True
    for check, msg in checks:
        if not check:
            print(f"VALIDATION FAILED: {msg}")
            all_ok = False

    return all_ok


def main() -> int:
    print("=" * 60)
    print("HTML Publication Generator")
    print("=" * 60)

    # Load data
    if not PUBS_FILE.exists():
        print(f"ERROR: Publications file not found: {PUBS_FILE}")
        return 1

    pubs = load_json(PUBS_FILE)
    overrides = load_json(OVERRIDES_FILE) if OVERRIDES_FILE.exists() else {}

    print(f"Loaded {len(pubs)} publications")

    # Sort pubs by year desc, then title
    pubs.sort(key=lambda p: (-p.get("year", 0), p.get("title", "")))

    # Generate HTML
    generated_html = generate_all_pubs_html(pubs, overrides)

    # Read current index.html
    if not INDEX_FILE.exists():
        print(f"ERROR: index.html not found: {INDEX_FILE}")
        return 1

    html_content = INDEX_FILE.read_text(encoding="utf-8")

    # Inject generated HTML
    try:
        new_html = inject_into_html(html_content, generated_html)
    except ValueError as e:
        print(f"ERROR: {e}")
        return 1

    # Validate
    if not validate_html(new_html):
        print("ERROR: Validation failed, not writing file")
        return 1

    # Update pub count display
    pub_count_display = overrides.get("pub_count_display", f"{len(pubs)}+ Publications")
    # Update the pub count badge in the header
    new_html = re.sub(
        r'(class="pub-count"[^>]*>)\s*[^<]*\s*(</a>)',
        f"\\1\n                        {pub_count_display}\n                    \\2",
        new_html,
    )

    # Write updated file
    INDEX_FILE.write_text(new_html, encoding="utf-8")
    print(f"Updated {INDEX_FILE}")
    print("HTML generation completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
