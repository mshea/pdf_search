#!/usr/bin/env python3
"""
PDF Search web interface.
Flask app with full-text search, folder browsing, and PDF serving.
"""

from html import escape as html_escape
import logging
import os
import re
import sqlite3
import sys
import threading
import time

from flask import Flask, render_template, request, send_file, jsonify

# Allow imports from the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from extractor import init_db, scan_directory

app = Flask(__name__)
logger = logging.getLogger(__name__)

# --- Indexer state ---
_indexer_lock = threading.Lock()
_indexer_status = {
    'running': False,
    'last_run': None,
    'message': '',
    'error': None,
}


def _run_indexer():
    """Run the extractor in a background thread."""
    with _indexer_lock:
        if _indexer_status['running']:
            return
        _indexer_status['running'] = True
        _indexer_status['error'] = None
        _indexer_status['message'] = 'Starting...'

    def _on_progress(msg):
        _indexer_status['message'] = msg

    try:
        init_db(config.DB_PATH)
        scan_directory(config.PDF_DIR, config.DB_PATH, progress_callback=_on_progress,
                       use_threads=True)
        _indexer_status['last_run'] = time.strftime('%Y-%m-%d %H:%M:%S')
        _indexer_status['message'] = ''
    except Exception as e:
        logger.exception("Indexer error")
        _indexer_status['error'] = str(e)
        _indexer_status['message'] = ''
    finally:
        _indexer_status['running'] = False


def _periodic_indexer(interval=3600):
    """Run the indexer on startup, then every `interval` seconds."""
    while True:
        _run_indexer()
        time.sleep(interval)


STOPWORDS = frozenset({
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from',
    'has', 'he', 'in', 'is', 'it', 'its', 'of', 'on', 'that', 'the',
    'to', 'was', 'will', 'with'
})


def get_db():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def format_size(size_bytes):
    if size_bytes is None:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


_PDF_DIR_PREFIX = config.PDF_DIR if config.PDF_DIR.endswith('/') else config.PDF_DIR + '/'


def _make_result(row):
    """Build a result dict from a database row."""
    return {
        'id': row['id'], 'filename': row['filename'],
        'path': row['pdf_path'].removeprefix(_PDF_DIR_PREFIX),
        'size': format_size(row['file_size']),
        'modified': row['modified_date'] or '',
        'snippet': '',
    }


def _highlight_excerpt(excerpt, terms):
    """Escape an excerpt and wrap matching terms in <mark> tags."""
    if not excerpt:
        return ''
    text = html_escape(excerpt.strip())
    for term in terms:
        pattern = re.compile(re.escape(html_escape(term)), re.IGNORECASE)
        text = pattern.sub(lambda m: f'<mark>{m.group()}</mark>', text)
    return '...' + text + '...'


def _escape_like(value):
    """Escape LIKE wildcard characters in a value."""
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def _parse_query(query):
    """Parse search query. Returns (search_words, path_filter, filename_only)."""
    words = []
    path_filter = None
    filename_only = False

    path_match = re.search(r'path:"([^"]+)"|path:(\S+)', query)
    if path_match:
        path_filter = path_match.group(1) or path_match.group(2)
        query = re.sub(r'path:"[^"]+"', '', query)
        query = re.sub(r'path:\S+', '', query)

    for token in query.split():
        if token.startswith('filename:'):
            words.append(token[9:])
            filename_only = True
        elif token.strip():
            words.append(token)

    return words, path_filter, filename_only


def _build_fts_query(raw):
    """Translate user search syntax into an FTS5 query string.

    Supported syntax:
      "exact phrase"   — phrase match
      -word            — exclude term (NOT)
      word1 OR word2   — match either term
      word*            — prefix match
      word1 NEAR/N word2 — proximity search
    """
    fts_parts = []

    # 1. Extract NEAR expressions (e.g. dragon NEAR/5 lair)
    near_re = r'(\S+)\s+NEAR/(\d+)\s+(\S+)'
    for m in re.finditer(near_re, raw, re.IGNORECASE):
        fts_parts.append(f'NEAR("{m.group(1)}" "{m.group(3)}", {m.group(2)})')
    raw = re.sub(near_re, '', raw, flags=re.IGNORECASE)

    # 2. Extract quoted phrases
    for m in re.finditer(r'"([^"]+)"', raw):
        fts_parts.append(f'"{m.group(1)}"')
    raw = re.sub(r'"[^"]*"', '', raw)

    # 3. Process remaining tokens
    tokens = raw.split()
    i = 0
    while i < len(tokens):
        token = tokens[i]

        # OR operator: combine previous part with next token
        if token.upper() == 'OR' and fts_parts and i + 1 < len(tokens):
            prev = fts_parts.pop()
            nxt = tokens[i + 1]
            if nxt.startswith('-'):
                fts_parts.append(prev)
            elif nxt.endswith('*'):
                fts_parts.append(f'{prev} OR {nxt}')
            else:
                fts_parts.append(f'{prev} OR "{nxt}"')
            i += 2
            continue

        # NOT: -word
        if token.startswith('-') and len(token) > 1:
            word = token[1:]
            fts_parts.append(f'NOT "{word}"')
            i += 1
            continue

        # Prefix: word*
        if token.endswith('*') and len(token) > 1:
            fts_parts.append(token)
            i += 1
            continue

        # Regular word — skip stopwords
        if token.lower() not in STOPWORDS and len(token) > 1:
            fts_parts.append(f'"{token}"')

        i += 1

    return ' '.join(fts_parts)


def do_search(query):
    """Run a full-text search. Returns a list of result dicts."""
    search_words, path_filter, filename_only = _parse_query(query)
    raw = ' '.join(search_words)

    fts_query = _build_fts_query(raw)
    if not fts_query:
        return []

    # Build filename query: strip NOT/NEAR/OR, keep phrases and plain words
    fn_phrases = re.findall(r'"([^"]+)"', raw)
    fn_remaining = re.sub(r'"[^"]*"', '', raw)
    fn_words = [w for w in fn_remaining.split()
                if not w.startswith('-') and w.upper() != 'OR'
                and not re.match(r'NEAR/\d+', w, re.IGNORECASE)
                and len(w) > 1 and w.lower() not in STOPWORDS]
    filename_parts = [f'filename:"{p}"' for p in fn_phrases]
    filename_parts += [f'filename:"{w.rstrip("*")}"' for w in fn_words]
    filename_query = ' '.join(filename_parts) if filename_parts else fts_query

    path_clause = ""
    params_extra = []
    if path_filter:
        path_clause = " AND d.pdf_path LIKE ? ESCAPE '\\'"
        params_extra = [f'%{_escape_like(path_filter)}%']

    conn = get_db()
    c = conn.cursor()
    results = []
    seen_ids = set()
    ranked_rows = []

    try:
        # Pass 1: rank without snippets (fast)
        # Filename matches first
        c.execute(f"""
            SELECT d.id, d.filename, d.pdf_path, d.file_size, d.modified_date,
                   -1000.0 as score
            FROM documents_fts
            JOIN documents d ON d.id = documents_fts.rowid
            WHERE documents_fts MATCH ?{path_clause}
            ORDER BY score
        """, [filename_query] + params_extra)

        for row in c.fetchall():
            seen_ids.add(row['id'])
            ranked_rows.append(row)

        # Content matches
        if not filename_only:
            c.execute(f"""
                SELECT d.id, d.filename, d.pdf_path, d.file_size, d.modified_date,
                       bm25(documents_fts, 10000.0, 1.0) as score
                FROM documents_fts
                JOIN documents d ON d.id = documents_fts.rowid
                WHERE documents_fts MATCH ?{path_clause}
                ORDER BY score
            """, [fts_query] + params_extra)

            for row in c.fetchall():
                if row['id'] not in seen_ids:
                    ranked_rows.append(row)

        # Pass 2: extract snippet windows in SQL (avoids slow FTS5 snippet())
        if ranked_rows:
            # Extract search terms for snippet highlighting
            terms = [w.strip('"').lower() for w in raw.split()
                     if w.upper() != 'OR' and not w.startswith('-')
                     and not re.match(r'NEAR/\d+', w, re.IGNORECASE)]
            first_term = terms[0] if terms else ''

            ids = [row['id'] for row in ranked_rows]
            placeholders = ','.join('?' * len(ids))
            c.execute(f"""
                SELECT rowid as id,
                       substr(content,
                              MAX(1, instr(LOWER(content), ?) - 80),
                              200) as excerpt
                FROM documents_fts
                WHERE rowid IN ({placeholders})
            """, [first_term] + ids)
            excerpt_map = {row['id']: row['excerpt'] for row in c.fetchall()}

            for row in ranked_rows:
                result = _make_result(row)
                excerpt = excerpt_map.get(row['id'], '')
                result['snippet'] = _highlight_excerpt(excerpt, terms)
                results.append(result)
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()

    return results


# --- Routes ---

@app.route('/')
def index():
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as count FROM documents")
        total_docs = c.fetchone()['count']
        conn.close()
    except sqlite3.OperationalError:
        total_docs = 0
    return render_template('index.html', total_docs=total_docs,
                           site_title=config.SITE_TITLE)


@app.route('/search')
def search():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'results': [], 'count': 0, 'query': ''})
    results = do_search(query)
    return jsonify({'results': results, 'count': len(results), 'query': query})


@app.route('/browse')
def browse():
    path = request.args.get('path', '').strip()
    base = _PDF_DIR_PREFIX
    full_path = base + path + '/' if path else base

    results = []
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("""
            SELECT id, filename, pdf_path, file_size, modified_date
            FROM documents WHERE pdf_path LIKE ? ESCAPE '\\'
            ORDER BY filename
        """, (_escape_like(full_path) + '%',))

        for row in c.fetchall():
            rel_from_folder = row['pdf_path'][len(full_path):]
            if '/' not in rel_from_folder:
                results.append(_make_result(row))
        conn.close()
    except sqlite3.OperationalError:
        pass
    return jsonify({'results': results, 'count': len(results), 'path': path})


@app.route('/pdf/<int:doc_id>')
def serve_pdf(doc_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT pdf_path, filename FROM documents WHERE id = ?", (doc_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return "PDF not found", 404
    if not os.path.exists(row['pdf_path']):
        return "PDF file not found on disk", 404
    return send_file(row['pdf_path'], mimetype='application/pdf', as_attachment=False)


@app.route('/stats')
def stats():
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as count FROM documents")
        total_docs = c.fetchone()['count']
        c.execute("SELECT SUM(file_size) as total_size FROM documents")
        total_size = c.fetchone()['total_size'] or 0
        conn.close()
    except sqlite3.OperationalError:
        total_docs = 0
        total_size = 0
    return jsonify({'total_documents': total_docs, 'total_size': format_size(total_size)})


@app.route('/folders')
def folders():
    path = request.args.get('path', '').strip()
    base = _PDF_DIR_PREFIX
    full_base = base + path + '/' if path else base

    folders_dict = {}
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT pdf_path FROM documents WHERE pdf_path LIKE ? ESCAPE '\\'",
                  (_escape_like(full_base) + '%',))

        for row in c.fetchall():
            rel = row['pdf_path'][len(full_base):]
            if '/' in rel:
                folder = rel.split('/')[0]
                folders_dict[folder] = folders_dict.get(folder, 0) + 1
        conn.close()
    except sqlite3.OperationalError:
        pass

    folders_list = [{'name': k, 'count': v} for k, v in sorted(folders_dict.items())]
    return jsonify({'folders': folders_list, 'current_path': path})


@app.route('/reindex', methods=['POST'])
def reindex():
    origin = request.headers.get('Origin', '')
    if origin and not origin.startswith(('http://192.168.', 'http://localhost', 'http://127.0.0.1', 'https://home.zinjalabs.com')):
        return jsonify({'error': 'forbidden'}), 403
    if _indexer_status['running']:
        return jsonify({'status': 'already_running'})
    t = threading.Thread(target=_run_indexer, daemon=True)
    t.start()
    return jsonify({'status': 'started'})


@app.route('/reindex/status')
def reindex_status():
    return jsonify(_indexer_status)


if __name__ == '__main__':
    # In debug mode, Flask's reloader spawns a child process. Only start the
    # indexer in the child (WERKZEUG_RUN_MAIN is set) or when not in debug mode.
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        threading.Thread(target=_periodic_indexer, daemon=True).start()
    app.run(host=config.HOST, port=config.PORT, debug=False)
