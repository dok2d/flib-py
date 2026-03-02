#!/usr/bin/env python3
"""
Telegram bot for searching and downloading books from the flib library.

Usage:
    python bot.py --config config.yaml
"""
import argparse
import html
import logging
import os
import sqlite3
import sys
from io import BytesIO
from zipfile import ZipFile

import yaml
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                           ContextTypes, MessageHandler, filters)

logger = logging.getLogger(__name__)


# ─── Helpers (shared with web-select.py logic) ───────────────────────────────

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
    """Normalize INPX genre: 'sf_history:sf_action:' → 'sf_history, sf_action'."""
    if not raw:
        return ''
    if ':' not in raw:
        return raw.strip()
    return ', '.join(g.strip() for g in raw.split(':') if g.strip())


def size_format(size_bytes):
    try:
        size_bytes = int(size_bytes)
    except (ValueError, TypeError):
        return '?'
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1048576:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / 1048576:.1f} MB"


# ─── Bot ─────────────────────────────────────────────────────────────────────

class BookBot:
    def __init__(self, config: dict):
        self.allowed_users = set(int(u) for u in config.get('allowed_users', []))
        self.db_path       = config['database_path']
        self.archives_path = config['archives_path']
        self.max_results   = int(config.get('max_results', 5))

    def _allowed(self, user_id: int) -> bool:
        return user_id in self.allowed_users

    def _get_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.create_function('py_lower', 1, lambda s: s.lower() if s else '')
        return conn

    def _search(self, query: str) -> list:
        tokens = [t for t in query.lower().split() if t]
        if not tokens:
            return []
        conditions = ' AND '.join(
            ['(py_lower(author) LIKE ? OR py_lower(genre) LIKE ?'
             ' OR py_lower(title) LIKE ? OR py_lower(lang) LIKE ?'
             ' OR py_lower(tags) LIKE ?)']
            * len(tokens)
        )
        params = tuple(f'%{t}%' for t in tokens for _ in range(5))
        conn = self._get_db()
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT author, genre, title, id, size, format, date, lang, tags, filename"
                f" FROM books WHERE {conditions} LIMIT {self.max_results}",
                params,
            )
            return cursor.fetchall()
        finally:
            conn.close()

    def _get_file(self, book_id: str, fmt: str):
        """Return (BytesIO, send_filename, error_str). On error BytesIO is None."""
        conn = self._get_db()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT filename, title, author FROM books WHERE id = ?", (book_id,))
            row = cursor.fetchone()
        finally:
            conn.close()

        if not row:
            return None, None, "Book not found in database."

        filename, book_title, book_author = row
        archive_name  = (filename.replace('.inp', '.zip')
                         if filename.endswith('.inp') else f"{filename}.zip")
        archive_path  = os.path.join(self.archives_path, archive_name)

        if not os.path.isfile(archive_path):
            return None, None, "Archive file not found on server."

        target_file = f"{book_id}.{fmt}"
        with ZipFile(archive_path, 'r') as zf:
            if target_file not in zf.namelist():
                return None, None, "Book file not found inside archive."
            content = zf.read(target_file)

        disp_author = format_author(book_author)
        name = f"{(book_title or book_id)[:60]} - {disp_author[:40]} [{book_id}].{fmt}"
        name = name.replace('/', '_').replace('\\', '_')
        return BytesIO(content), name, None

    # ── Handlers ─────────────────────────────────────────────────────────────

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._allowed(update.effective_user.id):
            return
        await update.message.reply_text(
            "📚 <b>Book search</b>\n\n"
            "Send any text to search. You can combine words from different fields:\n"
            "<code>Толстой война</code> — author + word in title\n"
            "<code>Лазарчук Транквилиум</code> — exact match across fields\n\n"
            f"Up to <b>{self.max_results}</b> results per query.",
            parse_mode='HTML',
        )

    async def on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._allowed(update.effective_user.id):
            return

        query   = update.message.text.strip()
        results = self._search(query)

        if not results:
            await update.message.reply_text("Nothing found. Try different search terms.")
            return

        for r in results:
            author, genre, title, book_id, size, fmt, date, lang, tags, _ = r
            disp_author = format_author(author)
            disp_genre  = format_genre(genre)

            lines = [f"📖 <b>{html.escape(str(title))}</b>"]
            if disp_author:
                lines.append(f"✍️ {html.escape(disp_author)}")
            if disp_genre:
                lines.append(f"🏷 {html.escape(disp_genre)}")
            info = f"📅 {html.escape(str(date))} · {html.escape(str(lang).upper())} · {size_format(size)}"
            lines.append(info)

            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"⬇ Download {str(fmt).upper()}",
                    callback_data=f"dl:{book_id}:{fmt}",
                )
            ]])

            await update.message.reply_text(
                '\n'.join(lines),
                parse_mode='HTML',
                reply_markup=keyboard,
            )

    async def on_download(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._allowed(update.effective_user.id):
            return

        query = update.callback_query
        await query.answer()

        try:
            _, book_id, fmt = query.data.split(':', 2)
        except ValueError:
            return

        wait_msg = await query.message.reply_text("⏳ Preparing file…")

        bio, send_name, error = self._get_file(book_id, fmt)
        if error:
            await wait_msg.edit_text(f"❌ {error}")
            return

        await wait_msg.delete()
        await query.message.reply_document(document=bio, filename=send_name)

    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        logger.exception("Unhandled exception", exc_info=context.error)


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Telegram book-search bot')
    parser.add_argument('--config', required=True,
                        help='Path to YAML configuration file')
    args = parser.parse_args()

    if not os.path.isfile(args.config):
        print(f"Error: config file '{args.config}' not found.", file=sys.stderr)
        sys.exit(1)

    with open(args.config) as f:
        config = yaml.safe_load(f)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s  %(name)-20s  %(levelname)s  %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    bot = BookBot(config)
    app = Application.builder().token(config['token']).build()

    app.add_handler(CommandHandler('start', bot.cmd_start))
    app.add_handler(CallbackQueryHandler(bot.on_download, pattern=r'^dl:'))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.on_message))
    app.add_error_handler(bot.on_error)

    logger.info("Bot started. Allowed users: %s", sorted(bot.allowed_users))
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
