from flask import Flask, render_template, request, send_file
import sqlite3
import os
from zipfile import ZipFile
import argparse

app = Flask(__name__)

# Конфигурация через переменные окружения
DATABASE_PATH = os.environ.get('DATABASE_PATH', 'books.db')
ARCHIVES_PATH = os.environ.get('ARCHIVES_PATH', 'archives')
HOST = os.environ.get('HOST', '127.0.0.1')
PORT = int(os.environ.get('PORT', '5000'))

def get_db(db_file=DATABASE_PATH):
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
    if not text:
        return ''
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
        conn = get_db()
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
                        'full_title': r[2],
                        'full_author': r[0]
                    } for r in results
                ]
            }
    
    return render_template('results.html', results=results, search_term=search_term, sort_by=sort_by, size_format=size_format)

@app.route('/download/<filename>/<title>.<format>')
def download(filename, title, format):
    try:
        archive_name = filename.replace('.inp', '.zip') if filename.endswith('.inp') else f"{filename}.zip"
        archive_path = os.path.join(ARCHIVES_PATH, archive_name)
        
        if not os.path.isfile(archive_path):
            return f"Archive not found: {archive_name}", 404

        with ZipFile(archive_path, 'r') as zip:
            target_file = f"{title}.{format}"
            
            if target_file not in zip.namelist():
                return f"File '{target_file}' not found in archive", 404

            temp_dir = "temp_extract"
            os.makedirs(temp_dir, exist_ok=True)
            
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT title, author FROM books WHERE id = ?", (title,))
            book_data = cursor.fetchone()
            conn.close()
            
            if not book_data:
                return "Book metadata not found", 404
            
            book_title, book_author = book_data
            short_title = truncate_text(book_title, 50)
            short_author = truncate_text(book_author, 30)
            
            output_filename = f"{short_title} - {short_author} [{title}].{format}"
            output_filename = output_filename.replace('/', '_').replace('\\', '_')
            
            max_filename_length = 100
            if len(output_filename) > max_filename_length:
                output_filename = f"{short_title} [{title}].{format}"
                if len(output_filename) > max_filename_length:
                    output_filename = f"{title}.{format}"
            
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

def parse_arguments():
    parser = argparse.ArgumentParser(description='Book search web application')
    parser.add_argument('--database', default=DATABASE_PATH, help='Path to SQLite database file')
    parser.add_argument('--archives-path', default=ARCHIVES_PATH, help='Path to archives directory')
    parser.add_argument('--host', default=HOST, help='Host address to bind to')
    parser.add_argument('--port', type=int, default=PORT, help='Port number to listen on')
    return parser.parse_args()

if __name__ == '__main__':
    args = parse_arguments()
    
    # Обновляем конфигурацию из аргументов командной строки
    DATABASE_PATH = args.database
    ARCHIVES_PATH = args.archives_path
    HOST = args.host
    PORT = args.port
    
    # Проверка существования необходимых файлов и директорий
    if not os.path.isfile(DATABASE_PATH):
        print(f"Error: Database file '{DATABASE_PATH}' not found.")
        exit(1)
    
    if not os.path.isdir(ARCHIVES_PATH):
        print(f"Error: Archives directory '{ARCHIVES_PATH}' not found.")
        exit(1)

    app.run(host=HOST, port=PORT, debug=True)

