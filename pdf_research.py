#!/usr/bin/env python3
"""
PDF research tool for LLM use.

Provides search, folder listing, browsing, and full-text retrieval
against the local PDF search API. Use this script for all research
operations — do not write new scripts.

Usage:
  python3 pdf_research.py search "query" [options]
  python3 pdf_research.py folders [path]
  python3 pdf_research.py browse [path]
  python3 pdf_research.py text <doc_id> [--query "terms"]
  python3 pdf_research.py stats
"""

import sys
import json
import urllib.request
import urllib.parse
import os

# Use config for port/host if available
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import config
    _PORT = config.PORT
    _HOST = "localhost"
except ImportError:
    _PORT = 5000
    _HOST = "localhost"

BASE_URL = f"http://{_HOST}:{_PORT}"


def _get(endpoint, params=None):
    url = BASE_URL + endpoint
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read())


# --- API calls ---

def research(query, limit=20, offset=0, passages=10, passage_offset=0):
    """Full-text search returning documents with extracted passages."""
    return _get("/api/research", {
        "q": query,
        "limit": limit,
        "offset": offset,
        "passages": passages,
        "passage_offset": passage_offset,
    })


def search(query, limit=20, offset=0):
    """Search returning document metadata and snippets (no full passages)."""
    return _get("/search", {"q": query, "limit": limit, "offset": offset})


def folders(path=""):
    """List subdirectories at the given path with file counts."""
    params = {}
    if path:
        params["path"] = path
    return _get("/folders", params)


def browse(path=""):
    """List PDF files directly in the given folder path."""
    params = {}
    if path:
        params["path"] = path
    return _get("/browse", params)


def stats():
    """Return database statistics (total documents, total size)."""
    return _get("/stats")


def text(doc_id):
    """Return the full extracted text of a document by ID."""
    return _get(f"/text/{doc_id}", {"raw": "1"})


# --- Output formatters ---

def print_research(data):
    total = data["total"]
    offset = data["offset"]
    limit = data["limit"]
    shown = len(data["results"])
    print(f"Query: {data['query']}")
    print(f"Total matching documents: {total}  (offset={offset}, limit={limit}, showing {shown})")
    if total > offset + limit:
        print(f"  -> More results available: use --offset {offset + limit}")
    print()
    for doc in data["results"]:
        tp = doc["total_passages"]
        po = doc["passage_offset"]
        shown_p = len(doc["passages"])
        print(f"=== {doc['path']}  [id={doc['id']}, passages={tp}] ===")
        if tp > po + shown_p:
            print(f"  -> More passages: use --passage-offset {po + shown_p}")
        for i, passage in enumerate(doc["passages"], po + 1):
            print(f"  [{i}] {passage.strip()}")
            print()


def print_folders(data):
    path = data.get("current_path", "")
    label = f'/{path}' if path else '(root)'
    print(f"Folders in {label}:")
    for f in data["folders"]:
        print(f"  {f['name']}/  ({f['count']} files)")
    if not data["folders"]:
        print("  (none)")


def print_browse(data):
    path = data.get("path", "")
    label = f'/{path}' if path else '(root)'
    print(f"Files in {label}:  ({data['count']} total)")
    for doc in data["results"]:
        print(f"  [{doc['id']}] {doc['filename']}  {doc['size']}  {doc['modified']}")


def print_stats(data):
    print(f"Total documents: {data['total_documents']}")
    print(f"Total size:      {data['total_size']}")


# --- CLI ---

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Query the local PDF research API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  search   QUERY            Keyword/phrase search with short snippets
  research QUERY            Deep search with full passage extraction (use for research)
  folders  [PATH]           List subdirectories at PATH (default: root)
  browse   [PATH]           List PDF files in PATH (default: root)
  stats                     Database statistics

Search syntax:
  "exact phrase"            phrase match
  -word                     exclude term
  word1 OR word2            either term
  word*                     prefix match
  path:"Folder Name"        restrict to folder
  filename:term             match filename only
  word1 NEAR/5 word2        proximity match

Research workflow:
  1. Run `folders` to discover available collections
  2. Run `research "topic"` or `research "topic" --path "Folder"` for a survey
  3. Paginate with --offset and --passage-offset to read more
  4. Use `browse "Folder"` to see specific files by ID
""")

    parser.add_argument("command", choices=["search", "research", "folders", "browse", "stats"],
                        help="Operation to perform")
    parser.add_argument("query_or_path", nargs="?", default="",
                        help="Search query (for search/research) or path (for folders/browse)")
    parser.add_argument("--path", default=None,
                        help="Folder filter for search/research (e.g. 'Shadow of the Weird Wizard')")
    parser.add_argument("--limit", type=int, default=20,
                        help="Max documents to return (default: 20)")
    parser.add_argument("--offset", type=int, default=0,
                        help="Document offset for pagination")
    parser.add_argument("--passages", type=int, default=10,
                        help="Max passages per document (default: 10)")
    parser.add_argument("--passage-offset", type=int, default=0, dest="passage_offset",
                        help="Passage offset for pagination within a document")
    parser.add_argument("--json", action="store_true",
                        help="Output raw JSON instead of formatted text")

    args = parser.parse_args()

    try:
        if args.command == "research":
            q = args.query_or_path
            if args.path:
                q = f'{q} path:"{args.path}"'
            data = research(q, limit=args.limit, offset=args.offset,
                            passages=args.passages, passage_offset=args.passage_offset)
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                print_research(data)

        elif args.command == "search":
            q = args.query_or_path
            if args.path:
                q = f'{q} path:"{args.path}"'
            data = _get("/search", {"q": q})
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                print(f"Query: {data['query']}")
                print(f"Total: {data['count']}\n")
                for r in data["results"]:
                    print(f"  [{r['id']}] {r['path']}  {r['size']}")
                    if r.get("snippet"):
                        print(f"       {r['snippet']}")
                    print()

        elif args.command == "folders":
            data = folders(args.query_or_path)
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                print_folders(data)

        elif args.command == "browse":
            data = browse(args.query_or_path)
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                print_browse(data)

        elif args.command == "stats":
            data = stats()
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                print_stats(data)

    except urllib.error.URLError as e:
        print(f"Error: cannot reach API at {BASE_URL} — is the server running?", file=sys.stderr)
        print(f"  {e}", file=sys.stderr)
        sys.exit(1)
