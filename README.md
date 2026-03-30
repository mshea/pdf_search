# PDF Search

Full-text search over a local PDF library with a web interface. Uses SQLite FTS5 for fast searching and `pdftotext` for text extraction. Only indexes PDFs with a text-layer – no OCR. Search results link directly to the PDF files, served through Flask from your configured PDF directory. Includes a tool-focused deep-research API to query topics and passages from sources. Disclaimer: This was totally vibe coded with Claude Code.

## Features

- Full-text search across thousands of PDFs
- Indexes 5,000 PDFs in roughly 10 minutes across three workers
- Folder browsing sidebar with filter and resizable width
- Mobile-friendly responsive layout
- Filename matches ranked above content matches
- Sort results by relevance, name, or date (toggle ascending/descending)
- Search syntax: `"exact phrase"`, `-exclude`, `OR`, `prefix*`, `NEAR/N`, `path:"folder"`, `filename:term`
- Text view for each PDF with on-the-fly cleanup of pdftotext output (paragraph rejoining, header/footer removal, whitespace normalization)
- Search term highlighting in text view with match count and prev/next navigation
- Research API (`/api/research?q=&limit=`) returns JSON passages for research tools
- AJAX-powered results (no page reloads)
- Automatic indexing on startup, hourly, and on demand from the UI
- Live indexing progress in the web UI
- Stale record cleanup on re-index
- CLI search tool

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

Each search result includes a `[text]` link that opens a cleaned, readable version of the PDF's extracted text. It's still very messy but readable. Clean text from PDFs is a really hard problem.

The cleanup pipeline:

- Removes repeated headers/footers (detected across form-feed page boundaries)
- Rejoins paragraph lines broken by column wrapping
- Fixes hyphenated line breaks (`word-\n` → `word`)
- Collapses excessive whitespace and blank lines

When opened from a search result, matching terms are highlighted with a match counter and prev/next navigation buttons.

## Research API

Includes a JSON API for researching topics across the entire indexed PDF library. Returns relevant passage excerpts (~1000 characters each) with enough surrounding context to answer questions, summarize information, or synthesize knowledge across multiple sources — without needing to read full documents.

### How to Research a Topic

**1. Start broad.** Search for the topic and scan the results to see which documents mention it and how deeply:

```
GET /api/research?q=szass+tam&passages=1
```

Each result includes `total_passages` — the number of unique, non-overlapping passage windows in that document. This tells you how much material each source has. A document with 13 passages is a deep source; one with 1 passage has a passing mention.

**2. Read the deep sources.** Request more passages from the documents with the highest `total_passages`:

```
GET /api/research?q=szass+tam&limit=5&passages=20
```

**3. Paginate for completeness.** If a document has more passages than you requested, use `passage_offset` to get the rest:

```
GET /api/research?q=szass+tam&limit=1&passages=10&passage_offset=10
```

**4. Paginate across documents.** If `total` exceeds your `limit`, use `offset` to get the next batch of documents:

```
GET /api/research?q=szass+tam&offset=20
```

**5. Refine with search syntax.** Narrow results using phrases, exclusions, path filters, and proximity operators (see Search Syntax above):

```
GET /api/research?q="szass+tam"+thay+-undead&path:"Forgotten+Realms"
```

### Request

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `q` | yes | — | Search query (supports all search syntax) |
| `limit` | no | 20 | Max documents to return (max 20) |
| `offset` | no | 0 | Skip this many documents (for paginating through results) |
| `passages` | no | 10 | Max passages per document (max 50) |
| `passage_offset` | no | 0 | Skip this many passages per document (for reading deeper into a source) |

### Response

```json
{
  "query": "szass tam",
  "total": 45,
  "offset": 0,
  "limit": 20,
  "results": [
    {
      "id": 1234,
      "filename": "Thay_Land_Of_The_Red_Wizards.pdf",
      "path": "Forgotten Realms/Thay_Land_Of_The_Red_Wizards.pdf",
      "total_passages": 13,
      "passage_offset": 0,
      "passages": [
        "...~1000 characters of text surrounding each match...",
        "...another distinct passage from the same document..."
      ]
    }
  ]
}
```

**Key fields:**

- `total` — total documents matching the query across the entire library. Use with `offset` to paginate.
- `total_passages` — total non-overlapping passages in this document for this query. Use with `passage_offset` to read deeper.
- `passage_offset` — which passage you started from in this document.
- `id` — document ID. Use to fetch the full cleaned text at `/text/<id>` or the original PDF at `/pdf/<id>`.

### Research Workflow Example

Researching "Larloch" across the library:

```
# Step 1: Survey — which sources mention Larloch and how deeply?
GET /api/research?q=larloch&passages=1

# Response shows:
#   Lost_Tales_of_Myth_Drannor.pdf — total_passages: 13
#   DDAL07-18 Turn Back the Endless Night — total_passages: 12
#   Volos_Guide_to_the_Sword_Coast.pdf — total_passages: 11
#   Lords_of_Darkness_(3e).pdf — total_passages: 11
#   DragonMagazine415.pdf — total_passages: 1

# Step 2: Read the deepest sources
GET /api/research?q=larloch&limit=5&passages=20

# Step 3: Get remaining passages from a deep source
GET /api/research?q=larloch&limit=1&passages=10&passage_offset=10

# Step 4: Check the next batch of documents
GET /api/research?q=larloch&offset=20&passages=1

# Step 5: Cross-reference with a related query
GET /api/research?q="warlock+crypt"+lich
```

## CLI Search

```bash
python3 search.py "search terms" [limit]
```

## License

CC0 1.0 Universal. See [LICENSE](LICENSE).
