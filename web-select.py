from flask import Flask, render_template, request, send_file
import sqlite3
import os
import html as _html
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZipInfo, ZIP_STORED, ZIP_DEFLATED
import argparse
import logging
import uuid
import base64
from io import BytesIO
from datetime import datetime, timezone

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

_FB2_NS   = 'http://www.gribuser.ru/xml/fictionbook/2.0'
_XLINK_NS = 'http://www.w3.org/1999/xlink'


# ─── Data normalization ───────────────────────────────────────────────────────

def format_author(raw):
    """Normalize INPX author: 'Last,First,Middle:' → 'First Middle Last'.
    Idempotent on already-clean values."""
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
    """Normalize INPX genre: 'sf_history:sf_action:' → 'sf_history, sf_action'."""
    if not raw:
        return ''
    if ':' not in raw:
        return raw.strip()
    return ', '.join(g.strip() for g in raw.split(':') if g.strip())


def _normalize(r):
    return (format_author(r[0]), format_genre(r[1])) + r[2:]


# ─── DB helpers ───────────────────────────────────────────────────────────────

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
        os.path.join(app.config['ARCHIVES_PATH'], archive_name))
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
            params)
        return [_normalize(r) for r in cursor.fetchall()]
    finally:
        conn.close()


# ─── FB2 parsing ──────────────────────────────────────────────────────────────

def _parse_fb2_images(root):
    """Extract embedded images.
    Returns:
        data_uris  – {#id: data_uri_str}  for inline HTML / web reader
        raw_images – {id: (bytes, mime)}  for EPUB as separate files
    """
    data_uris, raw_images = {}, {}
    for binary in root.iter(f'{{{_FB2_NS}}}binary'):
        img_id = binary.get('id', '')
        mime   = binary.get('content-type', 'image/jpeg')
        if img_id and binary.text:
            b64 = ''.join(binary.text.split())
            try:
                img_bytes = base64.b64decode(b64)
                raw_images[img_id]        = (img_bytes, mime)
                data_uris[f'#{img_id}']  = f'data:{mime};base64,{b64}'
            except Exception:
                pass
    return data_uris, raw_images


class _FB2Converter:
    """Converts an FB2 ElementTree to HTML, collecting TOC entries."""

    def __init__(self, images: dict):
        self.images      = images   # {href: src} — either data-URI or relative path
        self.toc         = []       # [{id, title, level}]
        self._sec_count  = 0
        self._sec_stack  = []       # ancestor section IDs

    def convert(self, root) -> str:
        parts = []
        for body in root.iter(f'{{{_FB2_NS}}}body'):
            if body.get('name') == 'notes':
                continue
            for child in body:
                parts.append(self._node(child))
        return '\n'.join(parts)

    def _node(self, elem) -> str:
        tag  = elem.tag.split('}')[1] if '}' in elem.tag else elem.tag
        text = _html.escape(elem.text or '')
        tail = _html.escape(elem.tail or '')

        # section: push ID before processing children so that nested title
        # can read self._sec_stack[-1]
        if tag == 'section':
            self._sec_count += 1
            sid = f's{self._sec_count}'
            self._sec_stack.append(sid)
            ch = ''.join(self._node(c) for c in elem)
            self._sec_stack.pop()
            return f'<section id="{sid}">{text}{ch}</section>{tail}'

        ch    = ''.join(self._node(c) for c in elem)
        inner = text + ch

        if tag == 'p':
            return f'<p>{inner}</p>{tail}'

        if tag == 'title':
            ttxt = ' '.join(elem.itertext()).strip()
            lvl  = len(self._sec_stack)
            pid  = self._sec_stack[-1] if self._sec_stack else None
            if ttxt and pid:
                self.toc.append({'id': pid, 'title': ttxt, 'level': lvl})
            htag = f'h{min(lvl + 1, 4)}' if lvl > 0 else 'h2'
            return f'<{htag} class="ch-title">{inner}</{htag}>{tail}'

        if tag == 'subtitle':
            return f'<h3 class="subtitle">{inner}</h3>{tail}'
        if tag == 'epigraph':
            return f'<blockquote class="epigraph">{inner}</blockquote>{tail}'
        if tag == 'cite':
            return f'<blockquote class="cite">{inner}</blockquote>{tail}'
        if tag == 'poem':
            return f'<div class="poem">{inner}</div>{tail}'
        if tag == 'stanza':
            return f'<div class="stanza">{inner}</div>{tail}'
        if tag in ('v', 'text-author'):
            return f'<div class="{_html.escape(tag)}">{inner}</div>{tail}'
        if tag == 'empty-line':
            return f'<br>{tail}'
        if tag == 'emphasis':
            return f'<em>{inner}</em>{tail}'
        if tag == 'strong':
            return f'<strong>{inner}</strong>{tail}'
        if tag == 'strikethrough':
            return f'<s>{inner}</s>{tail}'
        if tag == 'sup':
            return f'<sup>{inner}</sup>{tail}'
        if tag == 'sub':
            return f'<sub>{inner}</sub>{tail}'
        if tag == 'code':
            return f'<code>{inner}</code>{tail}'
        if tag == 'a':
            href = _html.escape(elem.get(f'{{{_XLINK_NS}}}href', '#'))
            return f'<a href="{href}">{inner}</a>{tail}'
        if tag == 'image':
            href = elem.get(f'{{{_XLINK_NS}}}href', '')
            src  = self.images.get(href, '')
            alt  = _html.escape(elem.get('alt', ''))
            if src:
                return (f'<figure class="book-img">'
                        f'<img src="{src}" alt="{alt}" loading="lazy">'
                        f'</figure>{tail}')
            return tail
        return f'{inner}{tail}'


def fb2_to_html(content: bytes):
    """Parse FB2 → (html_str, toc_list).
    Images are embedded as data-URIs for inline display."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        return f'<p class="error">Parse error: {_html.escape(str(e))}</p>', []
    data_uris, _ = _parse_fb2_images(root)
    conv = _FB2Converter(data_uris)
    return conv.convert(root), conv.toc


# ─── Conversion helpers ───────────────────────────────────────────────────────

def _txt_node(elem, lines):
    tag = elem.tag.split('}')[1] if '}' in elem.tag else elem.tag
    if tag == 'p':
        txt = ''.join(elem.itertext()).strip()
        if txt:
            lines.append('    ' + txt)
    elif tag == 'title':
        txt = ''.join(elem.itertext()).strip()
        if txt:
            lines.extend(['', txt, '─' * min(len(txt), 60), ''])
    elif tag == 'subtitle':
        txt = ''.join(elem.itertext()).strip()
        if txt:
            lines.extend(['', txt, ''])
    elif tag in ('v', 'text-author'):
        txt = ''.join(elem.itertext()).strip()
        if txt:
            lines.append('  ' + txt)
    elif tag == 'stanza':
        for c in elem:
            _txt_node(c, lines)
        lines.append('')
    elif tag == 'empty-line':
        lines.append('')
    elif tag == 'image':
        pass  # skip images in plain text
    else:
        for c in elem:
            _txt_node(c, lines)


def fb2_to_txt(content: bytes) -> str:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return ''
    lines = []
    for body in root.iter(f'{{{_FB2_NS}}}body'):
        if body.get('name') == 'notes':
            continue
        _txt_node(body, lines)
    # collapse consecutive blank lines
    result, prev_blank = [], False
    for line in lines:
        blank = not line.strip()
        if blank and prev_blank:
            continue
        result.append(line)
        prev_blank = blank
    return '\n'.join(result)


def fb2_to_epub(content: bytes, book_title: str, book_author: str) -> BytesIO:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise ValueError(f"Invalid FB2: {e}") from e

    _, raw_images = _parse_fb2_images(root)

    # In EPUB, images are separate ZIP entries; use relative paths in HTML
    epub_img_map = {f'#{img_id}': f'images/{img_id}'
                    for img_id in raw_images}
    conv      = _FB2Converter(epub_img_map)
    html_body = conv.convert(root)
    toc       = conv.toc

    book_id   = str(uuid.uuid4())
    now       = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    st        = _html.escape(book_title)
    sa        = _html.escape(book_author)

    # OPF manifest items for images
    img_manifest = '\n'.join(
        f'    <item id="img-{i}" href="images/{img_id}" media-type="{mime}"/>'
        for i, (img_id, (_, mime)) in enumerate(raw_images.items())
    )

    nav_items = '\n'.join(
        f'    <li><a href="content.xhtml#{e["id"]}">{_html.escape(e["title"])}</a></li>'
        for e in toc
    )

    content_xhtml = f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ru">
<head><meta charset="utf-8"/><title>{st}</title>
<link rel="stylesheet" href="css/style.css"/></head>
<body>
<h1 class="book-title">{st}</h1>
<p class="book-author">{sa}</p>
<hr/>
{html_body}
</body></html>'''

    nav_xhtml = f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="ru">
<head><meta charset="utf-8"/><title>Contents</title></head>
<body>
<nav epub:type="toc" id="toc">
  <h1>Contents</h1>
  <ol>{nav_items}</ol>
</nav>
</body></html>'''

    opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:identifier id="uid">urn:uuid:{book_id}</dc:identifier>
  <dc:title>{st}</dc:title>
  <dc:creator>{sa}</dc:creator>
  <dc:language>ru</dc:language>
  <meta property="dcterms:modified">{now}</meta>
</metadata>
<manifest>
  <item id="nav"  href="nav.xhtml"     media-type="application/xhtml+xml" properties="nav"/>
  <item id="css"  href="css/style.css" media-type="text/css"/>
  <item id="book" href="content.xhtml" media-type="application/xhtml+xml"/>
  {img_manifest}
</manifest>
<spine>
  <itemref idref="nav" linear="no"/>
  <itemref idref="book"/>
</spine>
</package>'''

    container_xml = '''<?xml version="1.0" encoding="utf-8"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf"
              media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>'''

    css = ('body{font-family:Georgia,serif;font-size:1em;line-height:1.7;margin:1em 2em}'
           'p{text-indent:1.5em;margin:0 0 .5em}'
           'h1.book-title{text-align:center;margin-bottom:.3em}'
           'p.book-author{text-align:center;font-style:italic;margin-bottom:2em;color:#555}'
           'h2.ch-title,h3.subtitle{text-align:center;margin:2em 0 1em}'
           'blockquote{margin:1em 2em;font-style:italic}'
           'figure.book-img{text-align:center;margin:1em 0}'
           'figure.book-img img{max-width:100%;height:auto}'
           '.poem{margin:1em 2em}.v{display:block}')

    buf = BytesIO()
    with ZipFile(buf, 'w', ZIP_DEFLATED) as zf:
        # mimetype MUST be first entry and uncompressed
        mi = ZipInfo('mimetype')
        mi.compress_type = ZIP_STORED
        zf.writestr(mi, b'application/epub+zip')
        zf.writestr('META-INF/container.xml', container_xml.encode())
        zf.writestr('OEBPS/content.opf',      opf.encode())
        zf.writestr('OEBPS/nav.xhtml',         nav_xhtml.encode())
        zf.writestr('OEBPS/content.xhtml',     content_xhtml.encode())
        zf.writestr('OEBPS/css/style.css',     css.encode())
        for img_id, (img_bytes, _) in raw_images.items():
            zf.writestr(f'OEBPS/images/{img_id}', img_bytes)
    buf.seek(0)
    return buf


def fb2_to_pdf(content: bytes, book_title: str, book_author: str) -> bytes:
    try:
        from weasyprint import HTML as WeasyHTML  # optional dependency
    except ImportError as exc:
        raise ImportError("weasyprint is not installed") from exc

    html_body, _ = fb2_to_html(content)
    st = _html.escape(book_title)
    sa = _html.escape(book_author)
    full_html = f'''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>
@page {{ margin: 2cm; }}
body {{ font-family: DejaVu Serif, Georgia, serif; font-size: 11pt; line-height: 1.7; }}
p {{ text-indent: 1.5em; margin: 0 0 .4em; }}
h1 {{ font-size: 1.6em; text-align: center; margin-bottom: .3em; }}
.book-author {{ text-align: center; font-style: italic; margin-bottom: 2em; color: #555; }}
h2.ch-title, h3.subtitle {{ text-align: center; margin: 2em 0 1em; }}
blockquote {{ margin: 1em 2em; font-style: italic; }}
figure.book-img {{ text-align: center; margin: 1em 0; }}
figure.book-img img {{ max-width: 100%; height: auto; }}
section {{ margin-bottom: 1em; }}
.poem {{ margin: 1em 2em; }} .v {{ display: block; }}
</style></head>
<body>
<h1>{st}</h1>
<p class="book-author">{sa}</p>
<hr>
{html_body}
</body></html>'''
    return WeasyHTML(string=full_html).write_pdf()


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route('/', methods=['GET', 'POST'])
def index():
    results, search_term, sort_by = [], '', 'title'

    if request.method == 'POST':
        search_term = request.form.get('search_term', '')
        sort_col    = VALID_SORT_COLUMNS.get(request.form.get('sort_by', 'title'), 'title')
        results     = _search(search_term, sort_col)

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return {
                'results': [
                    {
                        'id':           r[3],
                        'title':        r[2],
                        'author':       r[0],
                        'genre':        r[1],
                        'size':         size_format(r[4]),
                        'date':         r[6],
                        'language':     r[7],
                        'tags':         [r[8]] if r[8] else [],
                        'link':         f"/download/{r[9]}/{r[3]}.{r[5]}",
                        'read_link':    f"/read/{r[9]}/{r[3]}.{r[5]}"
                                        if r[5].lower() == 'fb2' else None,
                        'convert_base': f"/convert/{r[9]}/{r[3]}.{r[5]}"
                                        if r[5].lower() == 'fb2' else None,
                        'full_title':   r[2],
                        'full_author':  r[0],
                    }
                    for r in results
                ]
            }

    return render_template('results.html', results=results,
                           search_term=search_term, sort_by=sort_by,
                           size_format=size_format)


def _get_book_content(filename, title, format):
    """Common: validate path, open ZIP, return (raw_bytes, book_title, book_author)."""
    archive_path = safe_archive_path(filename)
    if archive_path is None:
        return None, None, None, ("Invalid filename", 400)
    if not os.path.isfile(archive_path):
        return None, None, None, ("Archive not found", 404)

    with ZipFile(archive_path, 'r') as zf:
        target = f"{title}.{format}"
        if target not in zf.namelist():
            return None, None, None, ("File not found in archive", 404)
        content = zf.read(target)

    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT title, author FROM books WHERE id = ?", (title,))
        row = cursor.fetchone()
    finally:
        conn.close()

    book_title  = row[0] if row else str(title)
    book_author = format_author(row[1]) if row else ''
    return content, book_title, book_author, None


@app.route('/download/<filename>/<title>.<format>')
def download(filename, title, format):
    try:
        archive_path = safe_archive_path(filename)
        if archive_path is None:
            return "Invalid filename", 400
        if not os.path.isfile(archive_path):
            return "Archive not found", 404

        with ZipFile(archive_path, 'r') as zf:
            target = f"{title}.{format}"
            if target not in zf.namelist():
                return "File not found in archive", 404

            conn = get_db()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT title, author FROM books WHERE id = ?", (title,))
                row = cursor.fetchone()
            finally:
                conn.close()

            if not row:
                return "Book metadata not found", 404

            short_title  = truncate_text(row[0], 50)
            short_author = truncate_text(format_author(row[1]), 30)
            out_name = f"{short_title} - {short_author} [{title}].{format}"
            out_name = out_name.replace('/', '_').replace('\\', '_')
            if len(out_name) > 100:
                out_name = f"{short_title} [{title}].{format}"
            if len(out_name) > 100:
                out_name = f"{title}.{format}"

            temp_dir = "temp_extract"
            os.makedirs(temp_dir, exist_ok=True)
            out_path = os.path.join(temp_dir, out_name)
            with open(out_path, 'wb') as f:
                f.write(zf.read(target))

            return send_file(out_path, as_attachment=True,
                             download_name=out_name,
                             mimetype=f"application/{format}")
    except Exception:
        logger.exception("Download failed for %s/%s.%s", filename, title, format)
        return "Internal server error", 500


@app.route('/read/<filename>/<title>.<format>')
def read_book(filename, title, format):
    if format.lower() != 'fb2':
        return "Online reading is only supported for FB2 format", 400
    try:
        content, book_title, book_author, err = _get_book_content(filename, title, format)
        if err:
            return err

        html_content, toc = fb2_to_html(content)
        return render_template(
            'reader.html',
            content=html_content,
            book_title=book_title,
            book_author=book_author,
            book_id=title,
            toc=toc,
            download_link=f"/download/{filename}/{title}.{format}",
            convert_base=f"/convert/{filename}/{title}.{format}",
        )
    except Exception:
        logger.exception("Read failed for %s/%s.%s", filename, title, format)
        return "Internal server error", 500


@app.route('/convert/<filename>/<title>.<format>/to/<target>')
def convert_book(filename, title, format, target):
    if format.lower() != 'fb2':
        return "Conversion is only supported for FB2 format", 400
    if target not in ('txt', 'epub', 'pdf'):
        return "Unsupported target format. Use: txt, epub, pdf", 400
    try:
        content, book_title, book_author, err = _get_book_content(filename, title, format)
        if err:
            return err

        safe_name = truncate_text(book_title, 60).replace('/', '_').replace('\\', '_')
        safe_name = f"{safe_name} [{title}]"

        if target == 'txt':
            txt = fb2_to_txt(content)
            return send_file(
                BytesIO(txt.encode('utf-8')),
                as_attachment=True,
                download_name=f"{safe_name}.txt",
                mimetype='text/plain; charset=utf-8',
            )

        if target == 'epub':
            buf = fb2_to_epub(content, book_title, book_author)
            return send_file(
                buf,
                as_attachment=True,
                download_name=f"{safe_name}.epub",
                mimetype='application/epub+zip',
            )

        if target == 'pdf':
            try:
                pdf_bytes = fb2_to_pdf(content, book_title, book_author)
            except ImportError:
                return ("PDF conversion requires weasyprint.\n"
                        "Install: pip install weasyprint\n"
                        "Or use the reader's 🖨 Print button to save as PDF."), 501
            return send_file(
                BytesIO(pdf_bytes),
                as_attachment=True,
                download_name=f"{safe_name}.pdf",
                mimetype='application/pdf',
            )

    except Exception:
        logger.exception("Convert failed for %s/%s.%s → %s",
                         filename, title, format, target)
        return "Internal server error", 500


def parse_arguments():
    parser = argparse.ArgumentParser(description='Book search web application')
    parser.add_argument('--database',      default=app.config['DATABASE_PATH'])
    parser.add_argument('--archives-path', default=app.config['ARCHIVES_PATH'])
    parser.add_argument('--host',          default=HOST)
    parser.add_argument('--port', type=int, default=PORT)
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
