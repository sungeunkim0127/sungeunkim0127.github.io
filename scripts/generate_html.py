#!/usr/bin/env python3
"""
Generate the publication data blob from publications.json.

Reads data/publications.json + data/overrides.json, condenses author names,
classifies each paper by domain (AI / translational / clinical), and emits a
JSON array inside a <script id="pub-data"> block between the AUTO-GENERATED
markers in index.html. The page's vanilla JS reads this blob to render both
the publication constellation and the filterable "All publications" list.
"""

import json
import re
import sys
from html import escape
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
PUBS_FILE = PROJECT_ROOT / "data" / "publications.json"
OVERRIDES_FILE = PROJECT_ROOT / "data" / "overrides.json"
META_FILE = PROJECT_ROOT / "data" / "scholar_meta.json"
INDEX_FILE = PROJECT_ROOT / "index.html"

START_MARKER = "<!-- AUTO-GENERATED-PUBS-START -->"
END_MARKER = "<!-- AUTO-GENERATED-PUBS-END -->"

# Domain classification keywords (lowercased substring match).
AI_VENUE_KEYWORDS = [
    "arxiv", "cvpr", "chil", "midl", "ml4h", "psb", "eacl", "mlhc", "aaai",
    "ieee", "pmlr", "neurips", "iclr", "icml", "acl arr", "workshop",
    "nejm ai", "nature medicine", "npj digit", "machine learning for health",
    "pacific symposium", "corl", "miccai",
]
AI_TITLE_KEYWORDS = [
    "deep learning", "machine learning", "llm", "large language model",
    "artificial intelligence", "benchmark", "vqa", "segmentation",
    "hallucination", "grounded reasoning", "reasoning", "gpt", "chatgpt",
    "voice ai", "agent", "vision language", "vision model", "simulator",
    "automated", "prediction", "predict", "metric for", "radiology education",
    "multi-agent", "sycophancy", "report generation", "question answering",
    "neural", "transformer", "foundation model",
]
ORTHO_VENUE_KEYWORDS = [
    "jbjs", "j bone joint", "bone joint j", "bone & joint", "kssta",
    "knee surg relat", "clin orthop", "arthroplasty", "the knee", "knee 20",
    "medicina", "skeletal radiol", "orthop", "arthrosc", "j exp orthop",
    "bmc musculoskelet", "bmc cancer", "sports med", "scientific reports",
    "sci rep", "orthopaedic", "nucl med", "j orthop",
]


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _is_initials(tok: str) -> bool:
    """True if a token looks like an initials block (e.g. 'SE', 'JM', 'H')."""
    t = tok.replace("-", "").replace(".", "")
    return t.isalpha() and t.isupper() and 1 <= len(t) <= 4


def _condense_one(name: str) -> str:
    """Condense a single author name to 'Surname Initials' (e.g. 'Sung Eun Kim' -> 'Kim SE')."""
    name = name.strip()
    if not name:
        return name
    if name in ("...", "…") or name.lower() in ("et al", "et al.") or "(" in name:
        return name
    marker = ""
    m = re.search(r"([*†]+)$", name)
    if m:
        marker = m.group(1)
        name = name[: -len(marker)].strip()
    tokens = name.split()
    if len(tokens) == 1:
        return name + marker
    first, last = tokens[0], tokens[-1]
    if _is_initials(last) and not _is_initials(first):
        return name + marker
    if len(tokens) == 2 and _is_initials(first) and len(first.replace(".", "")) >= 2 and not _is_initials(last):
        return f"{tokens[1]} {first}{marker}"
    surname = tokens[-1]
    initials = ""
    for given in tokens[:-1]:
        for part in given.split("-"):
            if part and part[0].isalpha():
                initials += part[0].upper()
    return f"{surname} {initials}{marker}"


def condense_authors(authors: str) -> str:
    """Normalize an author string to comma-separated 'Surname Initials' form."""
    if not authors:
        return authors
    if " and " in authors:
        parts = authors.split(" and ")
    else:
        parts = [p.strip() for p in authors.split(",")]
    return ", ".join(_condense_one(p) for p in parts if p.strip())


def classify_domain(pub: dict) -> str:
    """Classify a paper as 'ai', 'both' (translational), or 'ortho' (clinical)."""
    title = (pub.get("title") or "").lower()
    venue = (pub.get("venue") or "").lower()
    ai_venue = any(k in venue for k in AI_VENUE_KEYWORDS)
    ortho_venue = any(k in venue for k in ORTHO_VENUE_KEYWORDS)
    ai_method = any(k in title for k in AI_TITLE_KEYWORDS)
    if ai_venue and not ortho_venue:
        return "ai"
    if ortho_venue:
        return "both" if ai_method else "ortho"
    return "ai" if ai_method else "ortho"


def apply_overrides(pubs: list[dict], overrides: dict) -> list[dict]:
    """Apply per-publication field overrides from overrides.json."""
    pub_overrides = overrides.get("publication_overrides", {})
    for pub in pubs:
        pub_id = pub.get("id", "")
        if pub_id in pub_overrides:
            for key, value in pub_overrides[pub_id].items():
                pub[key] = value
    return pubs


def build_pub_records(pubs: list[dict], overrides: dict) -> list[dict]:
    """Build the cleaned, tagged publication records for the data blob."""
    exclude_ids = set(overrides.get("exclude_ids", []))
    tag_overrides = overrides.get("tag_overrides", {})  # id -> "ai" | "both" | "ortho"

    if exclude_ids:
        pubs = [p for p in pubs if p.get("id", "") not in exclude_ids]

    pubs = apply_overrides(pubs, overrides)

    records = []
    for pub in pubs:
        pub_id = pub.get("id", "")
        domain = tag_overrides.get(pub_id) or classify_domain(pub)
        tags = [domain]
        if pub.get("is_first_author", False):
            tags.append("first")
        records.append({
            "year": pub.get("year", 0),
            "title": pub.get("title", ""),
            "venue": pub.get("venue", ""),
            "authors": condense_authors(pub.get("authors", "")),
            "href": pub.get("url", ""),
            "tags": tags,
            "cites": pub.get("citation_count", 0) or 0,
        })

    # Sort by year desc, then title
    records.sort(key=lambda p: (-p.get("year", 0), p.get("title", "")))
    return records


def compute_total_citations(records: list[dict]) -> int:
    """Prefer the scraped profile total; fall back to the indexed sum."""
    if META_FILE.exists():
        try:
            meta = load_json(META_FILE)
            total = int(meta.get("total_citations", 0) or 0)
            if total:
                return total
        except Exception:
            pass
    return sum(int(r.get("cites", 0) or 0) for r in records)


def generate_data_block(records: list[dict]) -> str:
    """Render the <script id="pub-meta"> + <script id="pub-data"> JSON blocks."""
    meta = {"citations": compute_total_citations(records)}
    payload = json.dumps(records, ensure_ascii=False, indent=0)
    return (
        '                <script id="pub-meta" type="application/json">'
        f"{json.dumps(meta, ensure_ascii=False)}</script>\n"
        '                <script id="pub-data" type="application/json">\n'
        f"{payload}\n"
        "                </script>"
    )


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
    return pattern.sub(replacement, html_content)


def validate_html(html_content: str) -> bool:
    """Basic validation of the generated HTML."""
    checks = [
        (START_MARKER in html_content, "Start marker missing"),
        (END_MARKER in html_content, "End marker missing"),
        ("</html>" in html_content, "</html> tag missing"),
        ('id="pub-data"' in html_content, "pub-data block missing"),
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
    print("Publication Data Generator")
    print("=" * 60)

    if not PUBS_FILE.exists():
        print(f"ERROR: Publications file not found: {PUBS_FILE}")
        return 1

    pubs = load_json(PUBS_FILE)
    overrides = load_json(OVERRIDES_FILE) if OVERRIDES_FILE.exists() else {}
    print(f"Loaded {len(pubs)} publications")

    records = build_pub_records(pubs, overrides)
    print(f"Emitting {len(records)} publications after exclusions")

    data_block = generate_data_block(records)

    if not INDEX_FILE.exists():
        print(f"ERROR: index.html not found: {INDEX_FILE}")
        return 1

    html_content = INDEX_FILE.read_text(encoding="utf-8")
    try:
        new_html = inject_into_html(html_content, data_block)
    except ValueError as e:
        print(f"ERROR: {e}")
        return 1

    if not validate_html(new_html):
        print("ERROR: Validation failed, not writing file")
        return 1

    INDEX_FILE.write_text(new_html, encoding="utf-8")
    print(f"Updated {INDEX_FILE}")
    print("Publication data generation completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
