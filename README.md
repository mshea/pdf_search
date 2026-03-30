# PDF Search

Full-text search over a local PDF library with a web interface. Uses SQLite FTS5 for fast searching and `pdftotext` for text extraction. Only indexes PDFs with a text-layer — no OCR. Search results link directly to the PDF files, served through Flask from your configured PDF directory. Includes a research API and CLI tool for tool-based deep research across the library. Disclaimer: This was totally vibe coded with Claude Code.

## Features

- Full-text search across thousands of PDFs
- Indexes 5,000 PDFs in roughly 10 minutes with three workers
- Folder browsing sidebar with filter and resizable width
- Mobile-friendly responsive layout
- Filename matches ranked above content matches
- Sort results by relevance, name, or date (toggle ascending/descending)
- Search syntax: `"exact phrase"`, `-exclude`, `OR`, `prefix*`, `NEAR/N`, `path:"folder"`, `filename:term`
- Text view for each PDF with on-the-fly cleanup of pdftotext output (paragraph rejoining, header/footer removal, whitespace normalization)
- Search term highlighting in text view with match count and prev/next navigation
- AJAX-powered results (no page reloads)
- Automatic indexing on startup, hourly, and on demand from the UI
- Live indexing progress in the web UI
- Stale record cleanup on re-index
- Research API (`/api/research`) returns JSON passages for research tools
- `pdf_research.py` — single CLI script for all research operations (search, browse, folders, passages, stats)

## Requirements

- Python 3.8+
- Flask (`pip install flask`)
- `pdftotext` (from `poppler-utils`)

Install on Debian/Ubuntu:

```bash
sudo apt install poppler-utils
pip install flask
```

Install on macOS:

```bash
brew install poppler
pip install flask
```

## Setup

1. Clone this repo.
2. Copy `config.py.sample` to `config.py` and set `PDF_DIR` to your PDF directory.

```bash
cp config.py.sample config.py
```

3. Start the web server:

```bash
cd web
python3 app.py
```

4. Open `http://localhost:5555` in a browser.

The app automatically indexes your PDFs on startup. Progress is shown in the web UI. Once indexing completes, search is available immediately. New or changed PDFs are picked up automatically every hour, or you can click "update index" in the UI at any time.

You can also run the extractor standalone if needed:

```bash
python3 extractor.py
```

## Configuration

Edit `config.py` or set environment variables:

| Variable | Default | Description |
|---|---|---|
| `PDF_SEARCH_PDF_DIR` | `./pdfs` | Directory containing PDFs |
| `PDF_SEARCH_DB` | `./pdf_search.db` | SQLite database path |
| `PDF_SEARCH_HOST` | `0.0.0.0` | Web server bind address |
| `PDF_SEARCH_PORT` | `5555` | Web server port |
| `PDF_SEARCH_TITLE` | `PDF Search` | Site title in the web UI |
| `PDF_SEARCH_MAX_WORKERS` | `3` | Parallel workers for PDF extraction |

## Search Syntax

| Syntax | Example | Description |
|---|---|---|
| `"phrase"` | `"magic missile"` | Exact phrase match |
| `-word` | `dragon -chromatic` | Exclude results containing a word |
| `OR` | `wizard OR sorcerer` | Match either term |
| `word*` | `necro*` | Prefix match (necromancer, necromancy, etc.) |
| `NEAR/N` | `dragon NEAR/5 lair` | Words within N words of each other |
| `path:"folder"` | `path:"D&D 5e"` | Filter results to a folder |
| `filename:term` | `filename:dragon` | Search filenames only |

## Text View

Each search result includes a `[text]` link that opens a cleaned, readable version of the PDF's extracted text. Messy but readable – clean text from PDFs is a really hard problem.

When opened from a search result, matching terms are highlighted with a match counter and prev/next navigation buttons.

## Research Tool

`pdf_research.py` is the single script for all tool-based research. No need to write new scripts for each research topic.

```bash
python3 pdf_research.py research "query" [--path "Folder"] [--passages N] [--offset N] [--passage-offset N]
python3 pdf_research.py search "query"
python3 pdf_research.py folders [path]
python3 pdf_research.py browse [path]
python3 pdf_research.py stats
```

All commands accept `--json` for raw JSON output.

### Workflow

**1. Survey.** Find which documents cover the topic and how deeply:

```bash
python3 pdf_research.py folders
python3 pdf_research.py research "topic" --passages 1
```

Each result shows `total_passages` — non-overlapping passage windows in that document. High counts = deep source.

**2. Read deeply.** Pull more passages from the best sources:

```bash
python3 pdf_research.py research "topic" --passages 20
```

**3. Paginate.** Use `--passage-offset` for more passages within a document, `--offset` for more documents:

```bash
python3 pdf_research.py research "topic" --passage-offset 20 --passages 20
python3 pdf_research.py research "topic" --offset 20
```

**4. Use varied queries** — synonyms, related terms, mechanics, character names, location names, etc.

**5. Write results** to a markdown file with section-based citations (document name, relevant passage).

See `PDF Research Prompt.md` for a reusable prompt template.

### API Endpoints

- `GET /api/research` — passage extraction (`q`, `limit`, `offset`, `passages`, `passage_offset`)
- `GET /search` — search results with snippets (`q`)
- `GET /browse` — list files in a folder (`path`)
- `GET /folders` — list subdirectories with counts (`path`)
- `GET /stats` — document count and total size
- `GET /pdf/<id>` — serve PDF file
- `GET /text/<id>` — cleaned text view
- `POST /reindex` — trigger re-index (local origins only)
- `GET /reindex/status` — indexer status

## License

CC0 1.0 Universal. See [LICENSE](LICENSE).
