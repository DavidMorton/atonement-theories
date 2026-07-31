#!/usr/bin/env python3
"""
Static site builder for atonement-theories.

Reads:
  site.yaml           site chrome: domain, nav, landing cards
  content/*.md        prose pages (optional YAML front matter)
  content.yaml        map structure: node order, edges, camps
  nodes/*.yaml        one file per position on the map
  _templates/*.html   Jinja2 templates
  map/index.html      the interactive map (copied with %%TOKENS%% substituted)
  assets/**           PDFs, images, anything static

Writes everything to _site/, which is what gets deployed. Nothing generated
is ever committed — _site/ is gitignored and rebuilt from scratch each run.

    pip install -r requirements.txt
    python3 build.py

Add a new prose page by dropping a .md in content/. Add a new position on the
map by dropping a .yaml in nodes/ and listing it in content.yaml. Neither
requires touching this file.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path

try:
    import yaml
    import markdown
    from markdown.extensions import Extension
    from xml.etree import ElementTree as etree
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from markupsafe import Markup
except ImportError as exc:  # pragma: no cover - startup guard
    sys.exit(f"missing dependency: {exc}\n\n    pip install -r requirements.txt\n")

def wrap_tables_serializer(element):
    # 1. Use the original markdown serializer to convert the tree to a string
    html_string = markdown.serializers.to_html_string(element)
    
    # 2. Instantly wrap the tables using C-optimized regex
    return re.sub(
        r'(<table>.*?</table>)', 
        r'<div class="table-wrapper">\1</div>', 
        html_string, 
        flags=re.DOTALL
    )

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "_site"
CONTENT = ROOT / "content"
NODES = ROOT / "nodes"
TEMPLATES = ROOT / "_templates"
ASSETS = ROOT / "assets"
MAP_SRC = ROOT / "map" / "index.html"
DRIFT_YAML = ROOT / "drift.yaml"
DRIFT_SRC = ROOT / "drift" / "index.html"

# Markdown extensions. `footnotes` and `tables` are load-bearing for the long
# essay; `toc` gives us heading anchors *and* the sidebar contents list.
MD_EXTENSIONS = [
    "extra",        # tables, footnotes, attr_list, def_list, abbr
    "toc",
    "sane_lists",
    "smarty",       # curly quotes and em-dashes, matching the prose style
    "admonition"
]
MD_CONFIG = {
    "toc": {"permalink": "¶", "permalink_class": "anchor", "toc_depth": "2-3"},
    "smarty": {"smart_dashes": True, "smart_quotes": True, "smart_ellipses": True},
}

# The drift page's prose is short — a note under the diagram, or the body of an
# intro/outro screen — so it gets a smaller kit than the essay: emphasis, links,
# lists, the odd blockquote, and the same curly quotes as everywhere else. No
# toc (nothing to anchor), no footnotes (nowhere to put them).
DRIFT_MD_EXTENSIONS = ["extra", "sane_lists", "smarty"]
DRIFT_MD_CONFIG = {"smarty": MD_CONFIG["smarty"]}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def load_yaml(path: Path):
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def split_front_matter(text: str) -> tuple[dict, str]:
    """Return (metadata, body). Front matter is optional."""
    match = FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    meta = yaml.safe_load(match.group(1)) or {}
    return meta, text[match.end():]


def strip_html(html: str) -> str:
    return WS_RE.sub(" ", TAG_RE.sub(" ", html)).strip()


def excerpt(html: str, limit: int = 300) -> str:
    """First sentence-ish of rendered HTML, for meta descriptions."""
    text = strip_html(html)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    stop = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return (cut[: stop + 1] if stop > limit * 0.5 else cut.rsplit(" ", 1)[0] + "…").strip()


def reading_time(html: str) -> int:
    words = len(strip_html(html).split())
    return max(1, round(words / 220))


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# map data
# ---------------------------------------------------------------------------

def load_map(site: dict) -> dict:
    """Merge content.yaml with nodes/*.yaml into a single document.

    Order comes from the `node_files` manifest in content.yaml and matters —
    it's the order boxes are declared in the flowchart, which drives Mermaid's
    ranking and placement. Reordering the manifest reshuffles the diagram.
    """
    doc = load_yaml(ROOT / "content.yaml")
    doc.setdefault("nodes", [])
    doc.setdefault("extra_cards", [])

    seen_ids: dict[str, str] = {}
    for rel in doc.pop("node_files", []) or []:
        path = ROOT / rel
        if not path.exists():
            raise SystemExit(f"content.yaml lists {rel}, which does not exist")
        entry = load_yaml(path)
        if not entry or not entry.get("id"):
            print(f"  warn: {rel} has no `id` field — skipped")
            continue
        if entry["id"] in seen_ids:
            print(f"  warn: duplicate id {entry['id']} ({rel} and {seen_ids[entry['id']]})")
        seen_ids[entry["id"]] = rel

        # Slug comes from the filename, not the id: `owen-1647-death-of-death`
        # reads better in a URL and in search results than `OWE47`.
        entry["slug"] = path.stem
        entry["source"] = rel
        # A file with a `node:` block gets a box on the diagram; a card-only
        # file is reachable by link but unplotted.
        (doc["nodes"] if entry.get("node") else doc["extra_cards"]).append(entry)

    validate_map(doc)
    doc["site"] = {"baseurl": site.get("baseurl", "")}
    doc["generated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return doc


def validate_map(doc: dict) -> None:
    """Warn loudly rather than fail — a typo shouldn't block a deploy."""
    entries = doc["nodes"] + doc["extra_cards"]
    camps = {c["id"] for c in doc.get("camps", [])}
    for entry in entries:
        if not entry.get("card"):
            print(f"  warn: {entry['id']} has no `card:` block")
        if entry.get("camp") not in camps:
            print(f"  warn: {entry['id']} has unknown camp {entry.get('camp')!r}")
    boxed = {n["id"] for n in doc["nodes"]}
    for edge in doc.get("edges", []) or []:
        for side in ("from", "to"):
            if edge.get(side) not in boxed:
                print(f"  warn: edge {edge.get('from')} -> {edge.get('to')}: unknown {side}")


def render_drift_prose(doc: dict) -> None:
    """Turn drift.yaml's prose fields into HTML, in place.

    Rendered here rather than in the browser so the page doesn't have to ship a
    markdown parser for a few dozen sentences. The page inserts these as HTML,
    which is safe because the only author is this repo.

    Run this *after* validate_drift, which wants to see the raw text.
    """
    md = markdown.Markdown(extensions=DRIFT_MD_EXTENSIONS,
                           extension_configs=DRIFT_MD_CONFIG)

    def convert(text: str) -> str:
        md.reset()
        return md.convert(str(text).strip())

    for key in ("intro", "outro"):
        screen = doc.get(key)
        if isinstance(screen, dict) and screen.get("body"):
            screen["body"] = convert(screen["body"])

    for phase in doc.get("phases", []) or []:
        for step in phase.get("steps", []) or []:
            if step.get("note"):
                step["note"] = convert(step["note"])


def validate_drift(doc: dict) -> None:
    """Warn about drift.yaml problems. Never fails the build."""
    for key in ("intro", "outro"):
        screen = doc.get(key)
        if screen is not None and not (isinstance(screen, dict) and screen.get("body")):
            print(f"  ! drift: '{key}' is present but has no 'body'")
    for phase in doc.get("phases", []) or []:
        pid = phase.get("id", "?")
        if not phase.get("steps"):
            print(f"  ! drift: phase '{pid}' has no steps")
        prev_area = None
        for step in phase.get("steps", []) or []:
            sid = step.get("id", "?")
            for key in ("head", "note"):
                if not step.get(key):
                    print(f"  ! drift: {pid}/{sid} is missing '{key}'")
            live = step.get("live")
            if live:
                area = (live["x1"] - live["x0"]) * (live["y1"] - live["y0"])
                if prev_area and area > prev_area + 1e-9:
                    print(f"  ! drift: {pid}/{sid} live region grew; "
                          f"cuts should only ever halve it")
                prev_area = area


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def build() -> None:
    site = load_yaml(ROOT / "site.yaml")
    baseurl = (site.get("baseurl") or "").rstrip("/")
    site["baseurl"] = baseurl
    site["origin"] = (site.get("url") or "").rstrip("/")
    site["year"] = date.today().year

    def url(path: str = "/") -> str:
        """Site-relative URL -> served URL. Absolute URLs pass through."""
        if not path:
            return baseurl + "/"
        if path.startswith(("http://", "https://", "mailto:", "#")):
            return path
        return baseurl + "/" + path.lstrip("/")

    def absurl(path: str = "/") -> str:
        rel = url(path)
        return rel if rel.startswith("http") else site["origin"] + rel

    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals.update(site=site, url=url, absurl=absurl)

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    print("building…")

    # -- map data ----------------------------------------------------------
    doc = load_map(site)
    write(OUT / "map-data.json", json.dumps(doc, ensure_ascii=False, indent=1))
    print(f"  map-data.json ({len(doc['nodes'])} nodes, "
          f"{len(doc['extra_cards'])} cards, {len(doc.get('edges') or [])} edges)")

    # -- shared chrome -----------------------------------------------------
    # map/ and drift/ are copied rather than templated, but they still need
    # the same header as everything else. Render the partial once here and
    # hand it to them as a token, so there is exactly one copy of the nav.
    nav_tpl = env.get_template("_nav.html")

    def site_head(here: str) -> str:
        return nav_tpl.render(here=here)

    pages: list[dict] = []

    # -- prose pages -------------------------------------------------------
    md = markdown.Markdown(extensions=MD_EXTENSIONS, extension_configs=MD_CONFIG)
    md.serializer = wrap_tables_serializer
    for path in sorted(CONTENT.glob("*.md")):
        meta, body = split_front_matter(path.read_text(encoding="utf-8"))
        md.reset()
        html = md.convert(body)

        # Title: front matter wins, else the first H1, else the filename.
        title = meta.get("title")
        if not title:
            m = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
            title = m.group(1).strip() if m else path.stem.replace("-", " ").title()

        slug = meta.get("slug", path.stem)
        page = {
            "slug": slug,
            "url": f"/{slug}/",
            "title": title,
            "subtitle": meta.get("subtitle", ""),
            "description": meta.get("description") or excerpt(html),
            # Markup, not str: this is our own rendered markdown, and autoescape
            # would otherwise print the tags instead of applying them. Everything
            # else on the page stays escaped.
            "content": Markup(html),
            "toc": Markup(md.toc) if meta.get("toc") else "",
            "show_toc": bool(meta.get("toc")),
            "reading_time": reading_time(html),
            "show_reading_time": meta.get("reading_time", True),
            "layout": meta.get("layout", "page.html"),
            "noindex": meta.get("noindex", False),
            "sitemap": meta.get("sitemap", True),
            "priority": meta.get("priority", "0.8"),
            "updated": date.fromtimestamp(path.stat().st_mtime).isoformat(),
            "type": "article",
        }
        pages.append(page)

    for page in pages:
        template = env.get_template(page["layout"])
        write(OUT / page["slug"] / "index.html", template.render(page=page, pages=pages))
        print(f"  /{page['slug']}/  ({page['reading_time']} min)")

    # -- node pages --------------------------------------------------------
    camps = {c["id"]: c for c in doc.get("camps", [])}
    node_tpl = env.get_template("node.html")
    entries = doc["nodes"] + doc["extra_cards"]
    by_id = {e["id"]: e for e in entries}

    for entry in entries:
        card = entry.get("card") or {}
        # Neighbours give each node page real internal links, which is most of
        # what makes them worth generating in the first place.
        incoming = [by_id[e["from"]] for e in (doc.get("edges") or [])
                    if e.get("to") == entry["id"] and e.get("enabled", True)
                    and e.get("from") in by_id]
        outgoing = [by_id[e["to"]] for e in (doc.get("edges") or [])
                    if e.get("from") == entry["id"] and e.get("enabled", True)
                    and e.get("to") in by_id]
        edge_labels = {(e.get("from"), e.get("to")): e.get("label", "")
                       for e in (doc.get("edges") or [])}

        node_page = {
            "slug": entry["slug"],
            "url": f"/map/{entry['slug']}/",
            "title": card.get("title", entry["id"]),
            "description": card.get("headline") or card.get("problem", "")[:250],
            "updated": date.today().isoformat(),
            "priority": "0.6",
            "type": "article",
        }
        write(
            OUT / "map" / entry["slug"] / "index.html",
            node_tpl.render(
                page=node_page, entry=entry, card=card,
                camp=camps.get(entry.get("camp"), {}),
                incoming=incoming, outgoing=outgoing, edge_labels=edge_labels,
                pages=pages,
            ),
        )
        node_page["sitemap"] = True
        node_page["noindex"] = False
        pages.append(node_page)
    print(f"  /map/<slug>/  ({len(entries)} node pages)")

    # -- landing page ------------------------------------------------------
    home = {
        "slug": "", "url": "/", "title": site["title"],
        "description": site.get("description", ""),
        "updated": date.today().isoformat(), "priority": "1.0",
        "type": "website", "sitemap": True, "noindex": False,
    }
    write(OUT / "index.html", env.get_template("home.html").render(page=home, pages=pages))
    pages.insert(0, home)
    print("  /")

    # -- the map itself ----------------------------------------------------
    # Copied rather than templated: the file is full of JS with braces that
    # Jinja would fight over. Three tokens is all it needs.
    map_html = MAP_SRC.read_text(encoding="utf-8")
    map_html = (map_html
                .replace("%%SITEHEAD%%", site_head("/map/"))
                .replace("%%BASEURL%%", baseurl)
                .replace("%%ORIGIN%%", site["origin"]))
    write(OUT / "map" / "index.html", map_html)
    map_page = {
        "slug": "map", "url": "/map/", "title": "The atonement family tree",
        "description": doc.get("hint") or site.get("description", ""),
        "updated": date.today().isoformat(), "priority": "0.9",
        "type": "website", "sitemap": True, "noindex": False,
    }
    pages.append(map_page)
    print("  /map/")

    # -- the drift chart ---------------------------------------------------
    # Same pattern as the map: content lives in drift.yaml, the page is copied
    # rather than templated (its JS is full of braces Jinja would fight over),
    # and the data arrives as JSON it fetches at runtime.
    if DRIFT_YAML.exists() and DRIFT_SRC.exists():
        drift = load_yaml(DRIFT_YAML)
        validate_drift(drift)
        render_drift_prose(drift)
        write(OUT / "drift-data.json", json.dumps(drift, ensure_ascii=False))

        drift_html = DRIFT_SRC.read_text(encoding="utf-8")
        drift_html = (drift_html
                      .replace("%%SITEHEAD%%", site_head("/drift/"))
                      .replace("%%BASEURL%%", baseurl)
                      .replace("%%ORIGIN%%", site["origin"]))
        write(OUT / "drift" / "index.html", drift_html)

        pages.append({
            "slug": "drift", "url": "/drift/",
            "title": drift.get("title", "The Migration of the Claim"),
            "description": drift.get("tagline", ""),
            "updated": date.today().isoformat(), "priority": "0.8",
            "type": "website", "sitemap": True, "noindex": False,
        })
        print("  /drift/")

    # -- static assets -----------------------------------------------------
    if ASSETS.exists():
        shutil.copytree(ASSETS, OUT / "assets", dirs_exist_ok=True)
        count = sum(1 for p in (OUT / "assets").rglob("*") if p.is_file())
        print(f"  assets/ ({count} files)")

    # -- sitemap + robots --------------------------------------------------
    indexable = [p for p in pages if p.get("sitemap", True) and not p.get("noindex")]
    write(OUT / "sitemap.xml", env.get_template("sitemap.xml").render(pages=indexable))
    write(OUT / "robots.txt",
          "User-agent: *\nAllow: /\n\nSitemap: " + absurl("/sitemap.xml") + "\n")

    # GitHub Pages runs Jekyll over anything it serves unless told not to;
    # without this it strips files and folders beginning with an underscore.
    write(OUT / ".nojekyll", "")

    print(f"\ndone — {len(indexable)} indexable pages in {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    build()
