from flask import Flask, render_template, request, send_file
import sqlite3
import os
import html as _html
import xml.etree.ElementTree as ET
from zipfile import ZipFile
import argparse
import logging

app = Flask(__name__)

app.config['DATABASE_PATH'] = os.environ.get('DATABASE_PATH', 'books.db')
app.config['ARCHIVES_PATH'] = os.environ.get('ARCHIVES_PATH', 'archives')
HOST = os.environ.get('HOST', '127.0.0.1')
PORT = int(os.environ.get('PORT', '5000'))

logger = logging.getLogger(__name__)

VALID_SORT_COLUMNS = {
    'title':    'title',
    'author':   'author',
    'date':     'date',
    'size':     'size',
    'genre':    'genre',
    'language': 'lang',
    'id':       'id',
    'tags':     'tags',
}


# ─── Data normalization ───────────────────────────────────────────────────────

def format_author(raw):
    """Normalize INPX author: 'Last,First,Middle:' → 'First Middle Last'.
    Idempotent: already-clean values pass through unchanged."""
    if not raw:
        return ''
    if ':' not in raw and ',' not in raw:
        return raw.strip()
    authors = []
    for part in raw.split(':'):
        part = part.strip()
        if not part:
            continue
        components = [c.strip() for c in part.split(',') if c.strip()]
        if not components:
            continue
        if len(components) == 1:
            authors.append(components[0])
        else:
            last = components[0]
            rest = ' '.join(components[1:])
            authors.append(f'{rest} {last}'.strip())
    return ', '.join(authors) if authors else raw.strip()


def format_genre(raw):
    """Normalize INPX genre: 'sf_history:sf_action:' → 'sf_history, sf_action'.
    Idempotent: already-clean values pass through unchanged."""
    if not raw:
        return ''
    if ':' not in raw:
        return raw.strip()
    return ', '.join(g.strip() for g in raw.split(':') if g.strip())


def _normalize(r):
    """Return row with cleaned author and genre fields."""
    return (format_author(r[0]), format_genre(r[1])) + r[2:]


# ─── DB / helpers ─────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.create_function('py_lower', 1, lambda s: s.lower() if s else '')
    return conn


def safe_archive_path(filename):
    """Resolve archive path; return None if path traversal detected."""
    archives_base = os.path.realpath(app.config['ARCHIVES_PATH'])
    archive_name = (filename.replace('.inp', '.zip')
                    if filename.endswith('.inp') else f"{filename}.zip")
    archive_path = os.path.realpath(
        os.path.join(app.config['ARCHIVES_PATH'], archive_name)
    )
    if not archive_path.startswith(archives_base + os.sep):
        return None
    return archive_path


def size_format(size_bytes):
    try:
        size_bytes = int(size_bytes)
    except (ValueError, TypeError):
        return '?'
    if size_bytes < 1024:
        return f"{size_bytes} bytes"
    elif size_bytes < 1048576:
        return f"{size_bytes / 1024:.2f} Kb"
    else:
        return f"{size_bytes / 1048576:.2f} Mb"


def truncate_text(text, max_length=50):
    if not text:
        return ''
    if len(text) > max_length:
        return text[:max_length - 3] + '...'
    return text


def _search(search_term, sort_col):
    """Return normalized rows matching the multi-token search term."""
    tokens = [t for t in search_term.lower().split() if t]
    if not tokens:
        return []
    conditions = ' AND '.join(
        ['(py_lower(author) LIKE ? OR py_lower(genre) LIKE ?'
         ' OR py_lower(title) LIKE ? OR py_lower(format) LIKE ?'
         ' OR py_lower(lang) LIKE ? OR py_lower(tags) LIKE ?)']
        * len(tokens)
    )
    params = tuple(f'%{t}%' for t in tokens for _ in range(6))
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT * FROM books WHERE {conditions} ORDER BY {sort_col}",
            params,
        )
        return [_normalize(r) for r in cursor.fetchall()]
    finally:
        conn.close()


# ─── FB2 reader ───────────────────────────────────────────────────────────────

_FB2_NS   = 'http://www.gribuser.ru/xml/fictionbook/2.0'
_XLINK_NS = 'http://www.w3.org/1999/xlink'


def _fb2_node_to_html(elem):
    tag = elem.tag
    if '}' in tag:
        tag = tag.split('}')[1]

    text     = _html.escape(elem.text or '')
    tail     = _html.escape(elem.tail or '')
    children = ''.join(_fb2_node_to_html(child) for child in elem)
    inner    = text + children

    if tag == 'p':
        return f'<p>{inner}</p>{tail}'
    elif tag == 'section':
        return f'<section>{inner}</section>{tail}'
    elif tag == 'title':
        return f'<h2 class="chapter-title">{inner}</h2>{tail}'
    elif tag == 'subtitle':
        return f'<h3 class="subtitle">{inner}</h3>{tail}'
    elif tag == 'epigraph':
        return f'<blockquote class="epigraph">{inner}</blockquote>{tail}'
    elif tag == 'cite':
        return f'<blockquote class="cite">{inner}</blockquote>{tail}'
    elif tag == 'poem':
        return f'<div class="poem">{inner}</div>{tail}'
    elif tag == 'stanza':
        return f'<div class="stanza">{inner}</div>{tail}'
    elif tag in ('v', 'text-author'):
        return f'<div class="{_html.escape(tag)}">{inner}</div>{tail}'
    elif tag == 'empty-line':
        return f'<br>{tail}'
    elif tag == 'emphasis':
        return f'<em>{inner}</em>{tail}'
    elif tag == 'strong':
        return f'<strong>{inner}</strong>{tail}'
    elif tag == 'strikethrough':
        return f'<s>{inner}</s>{tail}'
    elif tag == 'sup':
        return f'<sup>{inner}</sup>{tail}'
    elif tag == 'sub':
        return f'<sub>{inner}</sub>{tail}'
    elif tag == 'code':
        return f'<code>{inner}</code>{tail}'
    elif tag == 'a':
        href = _html.escape(elem.get(f'{{{_XLINK_NS}}}href', '#'))
        return f'<a href="{href}">{inner}</a>{tail}'
    elif tag == 'image':
        return tail
    else:
        return f'{inner}{tail}'


def fb2_to_html(content):
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        return f'<p class="error">Error parsing book: {_html.escape(str(e))}</p>'
    parts = []
    for body in root.iter(f'{{{_FB2_NS}}}body'):
        if body.get('name') == 'notes':
            continue
        for child in body:
            parts.append(_fb2_node_to_html(child))
    return '\n'.join(parts)


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route('/', methods=['GET', 'POST'])
def index():
    results = []
    search_term = ""
    sort_by = "title"

    if request.method == 'POST':
        search_term = request.form.get('search_term', '')
        sort_by_raw = request.form.get('sort_by', 'title')
        sort_col    = VALID_SORT_COLUMNS.get(sort_by_raw, 'title')
        results     = _search(search_term, sort_col)

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return {
                'results': [
                    {
                        'id':         r[3],
                        'title':      r[2],
                        'author':     r[0],
                        'genre':      r[1],
                        'size':       size_format(r[4]),
                        'date':       r[6],
                        'language':   r[7],
                        'tags':       [r[8]] if r[8] else [],
                        'link':       f"/download/{r[9]}/{r[3]}.{r[5]}",
                        'read_link':  f"/read/{r[9]}/{r[3]}.{r[5]}"
                                      if r[5].lower() == 'fb2' else None,
                        'full_title':  r[2],
                        'full_author': r[0],
                    }
                    for r in results
                ]
            }

    return render_template('results.html', results=results,
                           search_term=search_term, sort_by=sort_by,
                           size_format=size_format)


@app.route('/download/<filename>/<title>.<format>')
def download(filename, title, format):
    try:
        archive_path = safe_archive_path(filename)
        if archive_path is None:
            return "Invalid filename", 400
        if not os.path.isfile(archive_path):
            return "Archive not found", 404

        with ZipFile(archive_path, 'r') as zip_file:
            target_file = f"{title}.{format}"
            if target_file not in zip_file.namelist():
                return "File not found in archive", 404

            conn = get_db()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT title, author FROM books WHERE id = ?", (title,))
                book_data = cursor.fetchone()
            finally:
                conn.close()

            if not book_data:
                return "Book metadata not found", 404

            book_title, book_author = book_data
            short_title  = truncate_text(format_author(book_title) if ',' in book_title else book_title, 50)
            short_author = truncate_text(format_author(book_author), 30)

            output_filename = f"{short_title} - {short_author} [{title}].{format}"
            output_filename = output_filename.replace('/', '_').replace('\\', '_')
            if len(output_filename) > 100:
                output_filename = f"{short_title} [{title}].{format}"
            if len(output_filename) > 100:
                output_filename = f"{title}.{format}"

            temp_dir = "temp_extract"
            os.makedirs(temp_dir, exist_ok=True)
            output_path = os.path.join(temp_dir, output_filename)

            with open(output_path, 'wb') as f:
                f.write(zip_file.read(target_file))

            return send_file(
                output_path,
                as_attachment=True,
                download_name=output_filename,
                mimetype=f"application/{format}",
            )

    except Exception:
        logger.exception("Download failed for %s/%s.%s", filename, title, format)
        return "Internal server error", 500


@app.route('/read/<filename>/<title>.<format>')
def read_book(filename, title, format):
    if format.lower() != 'fb2':
        return "Online reading is only supported for FB2 format", 400
    try:
        archive_path = safe_archive_path(filename)
        if archive_path is None:
            return "Invalid filename", 400
        if not os.path.isfile(archive_path):
            return "Archive not found", 404

        with ZipFile(archive_path, 'r') as zip_file:
            target_file = f"{title}.{format}"
            if target_file not in zip_file.namelist():
                return "File not found in archive", 404
            content = zip_file.read(target_file)

        conn = get_db()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT title, author FROM books WHERE id = ?", (title,))
            book_data = cursor.fetchone()
        finally:
            conn.close()

        book_title  = book_data[0] if book_data else title
        book_author = format_author(book_data[1]) if book_data else ""

        return render_template(
            'reader.html',
            content=fb2_to_html(content),
            book_title=book_title,
            book_author=book_author,
            download_link=f"/download/{filename}/{title}.{format}",
        )

    except Exception:
        logger.exception("Read failed for %s/%s.%s", filename, title, format)
        return "Internal server error", 500


def parse_arguments():
    parser = argparse.ArgumentParser(description='Book search web application')
    parser.add_argument('--database',      default=app.config['DATABASE_PATH'],
                        help='Path to SQLite database file')
    parser.add_argument('--archives-path', default=app.config['ARCHIVES_PATH'],
                        help='Path to archives directory')
    parser.add_argument('--host', default=HOST, help='Host address to bind to')
    parser.add_argument('--port', type=int, default=PORT, help='Port number to listen on')
    return parser.parse_args()


if __name__ == '__main__':
    args = parse_arguments()
    app.config['DATABASE_PATH'] = args.database
    app.config['ARCHIVES_PATH'] = args.archives_path

    if not os.path.isfile(app.config['DATABASE_PATH']):
        print(f"Error: Database file '{app.config['DATABASE_PATH']}' not found.")
        exit(1)
    if not os.path.isdir(app.config['ARCHIVES_PATH']):
        print(f"Error: Archives directory '{app.config['ARCHIVES_PATH']}' not found.")
        exit(1)

    app.run(host=args.host, port=args.port, debug=False)
