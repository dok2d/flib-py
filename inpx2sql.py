from os import path
from io import TextIOWrapper
from argparse import ArgumentParser
import logging
import zipfile
import sqlite3

parser = ArgumentParser(description='Converter inpx to sqlite.\n\nUse format:\n\ninpx2sqlite.py <filename.inpx> <out_db_file>.db')
parser.add_argument(
    '-d', '--debug',
    help="Show debug message",
    action="store_const", dest="loglevel", const=logging.DEBUG,
    default=logging.WARNING
)
parser.add_argument('-i', '-f', '--input', '--from', '--inpx', type=str, dest="inpx_file", help="Source inpx file", required=True)
parser.add_argument('-o', '--output', type=str, dest="out_file", help="Path to output database file", required=True)
args = parser.parse_args()
logging.basicConfig(level=args.loglevel)
logger = logging.getLogger()

if not path.isfile(args.inpx_file):
    print("File", args.inpx_file, "not found!")
    exit(1)

inpx_file = args.inpx_file
db_file = args.out_file

conn = sqlite3.connect(db_file)
c = conn.cursor()

c.execute('''CREATE TABLE IF NOT EXISTS books
             (author text, genre text, title text, id integer, size integer, format text, date text, lang text, tags text, filename text)''')

archive_info = zipfile.ZipFile(inpx_file,"r")

for filename in archive_info.namelist():
    if filename.endswith(".inp"):
        print(filename)
        with archive_info.open(filename) as file:
            for line in TextIOWrapper(file, 'utf-8'):
                author, genre, title, series, smth, id, size, smth, smth, format, date, lang, smth, tags, smth = line.strip().split("\x04")
                c.execute("INSERT INTO books VALUES (?,?,?,?,?,?,?,?,?,?)", (author, genre, title, id, size, format, date, lang, tags, filename))

conn.commit()
conn.close()
archive_info.close()
