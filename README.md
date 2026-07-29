# atonement-theories

Source for <https://davidmorton.github.io/atonement-theories/> — an essay and an
interactive family tree tracing how penal substitutionary atonement keeps
relocating its central difficulty instead of answering it.

## Working on it locally

```bash
pip install -r requirements.txt
./run.sh            # build, serve, open a browser
./run.sh --watch    # rebuild on every save
```

`run.sh` serves the built site under `/atonement-theories/` so local URLs match
production exactly. Nothing needs a separate local config.

## Where things live

| Path | What it is |
|---|---|
| `content/*.md` | Prose pages. One file per page; drop a new one in and it's live. |
| `content.yaml` | Map structure: node order, edges, camp colors. |
| `nodes/*.yaml` | One file per position on the map. |
| `site.yaml` | Site chrome: domain, nav, landing page cards. |
| `_templates/*.html` | Jinja2 layouts. |
| `assets/` | PDFs, images, CSS. Copied verbatim. |
| `map/index.html` | The interactive diagram. |
| `build.py` | Turns all of the above into `_site/`. |

`_site/` is generated and gitignored. It is never committed — GitHub Actions
builds it on every push to `main` and deploys the result.

## Adding things

**A new essay or page.** Create `content/whatever.md`. Front matter is optional
but worth setting:

```yaml
---
title: The title
subtitle: Optional standfirst
slug: whatever          # defaults to the filename
description: One or two sentences. Used for search results and link previews.
toc: true               # generate a table of contents
---
```

The page builds to `/whatever/`. Add it to `nav` or `cards` in `site.yaml` if
you want it linked from the header or landing page.

**A new position on the map.** Create `nodes/whoever.yaml` following the shape
of an existing one, then add it to the `node_files` list in `content.yaml`.
Order in that list matters — it's the order boxes are declared in the flowchart,
which drives how Mermaid ranks and places them.

Every node automatically gets its own page at `/map/<filename>/`, cross-linked
to its upstream and downstream neighbours, and is reachable from the diagram at
`/map/#<filename>`.

## Deployment

Pushing to `main` triggers `.github/workflows/deploy.yml`, which runs `build.py`
and publishes `_site/`. The repo's **Settings → Pages → Source** must be set to
**GitHub Actions** (not "Deploy from a branch").

Build warnings — a node with an unknown camp, an edge pointing at a node that
doesn't exist, a card with no `card:` block — show up in the Actions log. They
don't fail the build.
