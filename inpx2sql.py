import sys
from os import path
from io import TextIOWrapper
from argparse import ArgumentParser
import logging
import zipfile
import sqlite3


def main():
    parser = ArgumentParser(
        description='Converter inpx to sqlite.\n\nUse format:\n\ninpx2sqlite.py <filename.inpx> <out_db_file>.db'
    )
    parser.add_argument(
        '-d', '--debug',
        help="Show debug message",
        action="store_const", dest="loglevel", const=logging.DEBUG,
        default=logging.WARNING,
    )
    parser.add_argument('-i', '-f', '--input', '--from', '--inpx', type=str, dest="inpx_file",
                        help="Source inpx file", required=True)
    parser.add_argument('-o', '--output', type=str, dest="out_file",
                        help="Path to output database file", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=args.loglevel)
    logger = logging.getLogger()

    if not path.isfile(args.inpx_file):
        logger.error("File %s not found!", args.inpx_file)
        sys.exit(1)

    with sqlite3.connect(args.out_file) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS books
                     (author text, genre text, title text, id integer,
                      size integer, format text, date text, lang text,
                      tags text, filename text)''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_books_id     ON books (id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_books_author ON books (author)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_books_title  ON books (title)')

        with zipfile.ZipFile(args.inpx_file, 'r') as archive:
            for filename in archive.namelist():
                if not filename.endswith('.inp'):
                    continue
                print(filename)
                rows = []
                with archive.open(filename) as file:
                    for line in TextIOWrapper(file, 'utf-8'):
                        try:
                            parts = line.strip().split('\x04')
                            author, genre, title = parts[0], parts[1], parts[2]
                            book_id, size, fmt   = parts[5], parts[6], parts[9]
                            date, lang, tags     = parts[10], parts[11], parts[13]
                            rows.append((author, genre, title, book_id, size,
                                         fmt, date, lang, tags, filename))
                        except (IndexError, ValueError):
                            logger.warning("Skipping malformed line in %s: %r",
                                           filename, line[:80])
                c.executemany("INSERT INTO books VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()


if __name__ == '__main__':
    main()
