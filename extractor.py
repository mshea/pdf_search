#!/usr/bin/env python3
"""
PDF text extractor and indexer.
Extracts text from PDFs using pdftotext and stores it in SQLite with FTS5.
"""

import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import shutil

os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

import config

BATCH_SIZE = 50

# Locate pdftotext binary, checking common Homebrew paths if needed
PDFTOTEXT = shutil.which('pdftotext')
if not PDFTOTEXT:
    for p in ['/opt/homebrew/bin/pdftotext', '/usr/local/bin/pdftotext']:
        if os.path.isfile(p):
            PDFTOTEXT = p
            break


def init_db(db_path):
    """Initialize SQLite database with FTS5."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pdf_path TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL,
            extracted_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            file_size INTEGER,
            modified_date TIMESTAMP
        )
    """)
    # Add modified_date column if missing (for existing databases)
    c.execute("PRAGMA table_info(documents)")
    columns = {row[1] for row in c.fetchall()}
    if 'modified_date' not in columns:
        c.execute("ALTER TABLE documents ADD COLUMN modified_date TIMESTAMP")
        conn.commit()

    c.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
            filename,
            content,
            content_rowid=id
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS failed_extractions (
            pdf_path TEXT UNIQUE NOT NULL,
            file_size INTEGER,
            modified_date TIMESTAMP,
            failed_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def extract_text(pdf_path):
    """Extract text from a PDF using pdftotext."""
    if not PDFTOTEXT:
        return None
    try:
        result = subprocess.run(
            [PDFTOTEXT, '-enc', 'UTF-8', pdf_path, '-'],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode == 0:
            return result.stdout
        return None
    except (subprocess.TimeoutExpired, Exception):
        return None


def _extract_worker(pdf_path):
    """Worker function for parallel extraction. Runs in a subprocess."""
    pdf_path = str(Path(pdf_path).resolve())
    filename = os.path.basename(pdf_path)
    text = extract_text(pdf_path)
    if text is None:
        return None

    stat = os.stat(pdf_path)
    return {
        'pdf_path': pdf_path, 'filename': filename, 'text': text,
        'file_size': stat.st_size,
        'modified_date': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
    }


def scan_directory(directory, db_path, progress_callback=None, use_threads=False):
    """Scan a directory tree for PDFs and index them."""
    def _progress(msg):
        if progress_callback:
            progress_callback(msg)
        print(msg)

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # Bulk-load existing records into memory for fast skip-checks
    c.execute("SELECT pdf_path, id, file_size, modified_date FROM documents")
    known = {row[0]: (row[1], row[2], row[3]) for row in c.fetchall()}
    known_paths = set(known.keys())

    # Load previously failed extractions
    c.execute("SELECT pdf_path, file_size, modified_date FROM failed_extractions")
    failed = {row[0]: (row[1], row[2]) for row in c.fetchall()}

    # Collect all PDFs on disk
    pdf_files = []
    disk_paths = set()
    for root, dirs, files in os.walk(directory):
        for f in files:
            if f.lower().endswith('.pdf'):
                p = str(Path(os.path.join(root, f)).resolve())
                pdf_files.append(p)
                disk_paths.add(p)

    _progress(f"Scanning {len(pdf_files)} PDF files...")

    # Filter to only files that need processing
    to_process = []
    for pdf_path in pdf_files:
        try:
            stat = os.stat(pdf_path)
        except OSError:
            continue
        size = stat.st_size
        mdate = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')

        # Skip previously failed files (unless the file has changed)
        prev_fail = failed.get(pdf_path)
        if prev_fail and prev_fail[0] == size and prev_fail[1] == mdate:
            continue

        # Skip already-indexed files (unless the file has changed)
        existing = known.get(pdf_path)
        if existing:
            _, existing_size, existing_mdate = existing
            if size == existing_size and mdate == existing_mdate:
                continue

        to_process.append(pdf_path)

    if to_process:
        _progress(f"Indexing 0 of {len(to_process)} new PDFs...")
    else:
        _progress("No new PDFs to process")

    # Parallel extraction + serial DB writes
    processed = 0
    batch_count = 0
    Executor = ThreadPoolExecutor if use_threads else ProcessPoolExecutor
    with Executor(max_workers=config.MAX_WORKERS) as pool:
        futures = {pool.submit(_extract_worker, p): p for p in to_process}
        for future in as_completed(futures):
            result = future.result()
            if result is None:
                src = futures[future]
                print(f"  Failed: {os.path.basename(src)}")
                try:
                    st = os.stat(src)
                    md = datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                    c.execute("""
                        INSERT OR REPLACE INTO failed_extractions
                        (pdf_path, file_size, modified_date, failed_date)
                        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    """, (src, st.st_size, md))
                    conn.commit()
                except OSError:
                    pass
                continue

            pdf_path = result['pdf_path']
            existing = known.get(pdf_path)
            print(f"  Indexed: {result['filename']}")

            if existing:
                doc_id = existing[0]
                c.execute("""
                    UPDATE documents
                    SET extracted_date = CURRENT_TIMESTAMP,
                        file_size = ?, modified_date = ?
                    WHERE id = ?
                """, (result['file_size'], result['modified_date'], doc_id))
                c.execute("DELETE FROM documents_fts WHERE rowid = ?", (doc_id,))
            else:
                c.execute("""
                    INSERT INTO documents (pdf_path, filename, file_size, modified_date)
                    VALUES (?, ?, ?, ?)
                """, (pdf_path, result['filename'],
                      result['file_size'], result['modified_date']))
                doc_id = c.lastrowid

            c.execute("""
                INSERT INTO documents_fts (rowid, filename, content)
                VALUES (?, ?, ?)
            """, (doc_id, result['filename'], result['text']))

            processed += 1
            batch_count += 1
            _progress(f"Indexing {processed} of {len(to_process)} new PDFs...")
            if batch_count >= BATCH_SIZE:
                conn.commit()
                batch_count = 0

    if batch_count > 0:
        conn.commit()

    # Remove stale failure records for PDFs no longer on disk
    stale_failures = set(failed.keys()) - disk_paths
    if stale_failures:
        for path in stale_failures:
            c.execute("DELETE FROM failed_extractions WHERE pdf_path = ?", (path,))
        conn.commit()

    # Remove stale records for PDFs no longer on disk
    stale = known_paths - disk_paths
    if stale:
        _progress(f"Removing {len(stale)} stale records")
        for path in stale:
            doc_id = known[path][0]
            c.execute("DELETE FROM documents_fts WHERE rowid = ?", (doc_id,))
            c.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        conn.commit()

    conn.close()
    print(f"Processed {processed} new/updated PDFs")
    if stale:
        print(f"Removed {len(stale)} stale records")
    print(f"Database: {db_path}")


if __name__ == "__main__":
    if not PDFTOTEXT:
        print("Error: pdftotext not found. Install poppler-utils (Linux) or poppler (macOS).")
        sys.exit(1)

    pdf_dir = sys.argv[1] if len(sys.argv) > 1 else config.PDF_DIR

    if not os.path.isdir(pdf_dir):
        print(f"Error: directory not found: {pdf_dir}")
        sys.exit(1)

    init_db(config.DB_PATH)

    print(f"Scanning: {pdf_dir}\n")
    scan_directory(pdf_dir, config.DB_PATH)
