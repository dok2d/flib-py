import os
import io
import argparse
import zipfile
import sqlite3

parser = argparse.ArgumentParser(description='Converter inpx to sqlite.\n\nUse format:\n\ninpx2sqlite.py <filename.inpx> <out_db_file>.db')
args = parser.parse_args()

directory = os.curdir
db_name = "output_db.db"

conn = sqlite3.connect(db_name)
c = conn.cursor()

c.execute('''CREATE TABLE IF NOT EXISTS books
             (author text, genre text, title text, id integer, size integer, format text, date text, lang text, tags text, filename text)''')

archive_info = zipfile.ZipFile("flibusta_fb2_local.inpx","r")

for filename in archive_info.namelist():
    if filename.endswith(".inp"):
        print(filename)
        with archive_info.open(filename) as file:
            for line in io.TextIOWrapper(file, 'utf-8'):
                author, genre, title, series, smth, id, size, smth, smth, format, date, lang, smth, tags, smth = line.strip().split("\x04")
                c.execute("INSERT INTO books VALUES (?,?,?,?,?,?,?,?,?,?)", (author, genre, title, id, size, format, date, lang, tags, filename))

conn.commit()
conn.close()
archive_info.close()
