# web-select.py
from flask import Flask, render_template, request, send_file
import sqlite3
import sys
import argparse
from zipfile import ZipFile
import os
import hashlib

app = Flask(__name__)

def get_db(db_file):
    conn = sqlite3.connect(db_file)
    return conn

def size_format(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} bytes"
    elif size_bytes < 1048576:
        return f"{size_bytes / 1024:.2f} Kb"
    else:
        return f"{size_bytes / 1048576:.2f} Mb"

def truncate_text(text, max_length=50):
    if len(text) > max_length:
        return text[:max_length-3] + '...'
    return text

@app.route('/', methods=['GET', 'POST'])
def index():
    results = []
    search_term = ""
    sort_by = "title"
    
    if request.method == 'POST':
        search_term = request.form.get('search_term', '')
        sort_by = request.form.get('sort_by', 'title')
        conn = get_db(args.database)
        cursor = conn.cursor()
        query = """
            SELECT * FROM books 
            WHERE author LIKE ? OR genre LIKE ? OR title LIKE ? OR format LIKE ? OR lang LIKE ? OR tags LIKE ? 
            ORDER BY ?
        """
        cursor.execute(query, (
            f'%{search_term}%', f'%{search_term}%', f'%{search_term}%', 
            f'%{search_term}%', f'%{search_term}%', f'%{search_term}%', 
            sort_by
        ))
        results = cursor.fetchall()
        conn.close()
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return {
                'results': [
                    {
                        'id': r[3],
                        'title': r[2],
                        'author': r[0],
                        'genre': r[1],
                        'size': size_format(r[4]),
                        'date': r[6],
                        'language': r[7],
                        'tags': [r[8]] if r[8] else [],
                        'link': f"/download/{r[9]}/{r[3]}.{r[5]}",
                        'full_title': r[2],  # Сохраняем полное название для tooltip
                        'full_author': r[0]  # Сохраняем полное имя автора для tooltip
                    } for r in results
                ]
            }
    
    return render_template('results.html', results=results, search_term=search_term, sort_by=sort_by, size_format=size_format)

@app.route('/download/<filename>/<title>.<format>')
def download(filename, title, format):
    try:
        archive_name = filename.replace('.inp', '.zip') if filename.endswith('.inp') else f"{filename}.zip"
        archive_path = os.path.join(args.archives_path, archive_name)

        print(f"[DEBUG] Archive path: {archive_path}")
        print(f"[DEBUG] Looking for file: {title}.{format} in archive")

        if not os.path.isfile(archive_path):
            return f"Archive not found: {archive_name}", 404

        with ZipFile(archive_path, 'r') as zip:
            target_file = f"{title}.{format}"
            
            if target_file not in zip.namelist():
                return f"File '{target_file}' not found in archive", 404

            temp_dir = "temp_extract"
            os.makedirs(temp_dir, exist_ok=True)
            
            conn = get_db(args.database)
            cursor = conn.cursor()
            cursor.execute("SELECT title, author FROM books WHERE id = ?", (title,))
            book_data = cursor.fetchone()
            conn.close()
            
            if not book_data:
                return "Book metadata not found", 404
            
            book_title, book_author = book_data
            
            # Ограничиваем длину названия и автора
            max_filename_length = 100  # Максимальная длина имени файла
            short_title = truncate_text(book_title, 50)
            short_author = truncate_text(book_author, 30)
            
            # Создаем хеш для уникальности
            content_hash = hashlib.md5(f"{book_title}{book_author}".encode()).hexdigest()[:8]
            
            output_filename = f"{short_title} - {short_author} [{content_hash}].{format}"
            output_filename = output_filename.replace('/', '_').replace('\\', '_')  # Удаляем недопустимые символы
            
            # Если имя все еще слишком длинное, обрезаем его
            if len(output_filename) > max_filename_length:
                output_filename = output_filename[:max_filename_length-8] + f" [{content_hash}].{format}"
            
            output_path = os.path.join(temp_dir, output_filename)
            
            file_content = zip.read(target_file)
            with open(output_path, 'wb') as f:
                f.write(file_content)
            
            return send_file(
                output_path,
                as_attachment=True,
                download_name=output_filename,
                mimetype=f"application/{format}"
            )
            
    except Exception as e:
        print(f"[ERROR] Download failed: {str(e)}")
        return f"Error: {str(e)}", 500

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Book search web application')
    parser.add_argument('--database', required=True, help='Path to SQLite database file')
    parser.add_argument('--host', default='127.0.0.1', help='Host address to run the server on')
    parser.add_argument('--port', type=int, default=5000, help='Port number to run the server on')
    parser.add_argument('--archives-path', required=True, help='Path to the directory containing book archives (.zip)')
    args = parser.parse_args()

    if not os.path.isfile(args.database):
        print(f"Error: Database file '{args.database}' not found.")
        sys.exit(1)
    
    if not os.path.isdir(args.archives_path):
        print(f"Error: Archives directory '{args.archives_path}' not found.")
        sys.exit(1)

    app.run(host=args.host, port=args.port, debug=True)