# flib-py

Инструменты для работы с библиотекой в формате INPX:
конвертация каталога в SQLite, веб-поиск с онлайн-чтением FB2, Telegram-бот.

### Статус задач

- [x] Конвертация INPX → SQLite
- [x] Веб-поисковик с сортировкой и пагинацией
- [x] Выгрузка книги из ZIP-архива
- [x] Онлайн-чтение FB2 в браузере
- [x] Закладки (localStorage)
- [x] Telegram-бот с поиском и загрузкой
- [x] Dockerfile
- [ ] Конвертация FB2 в другой формат (txt, pdf, epub)
- [ ] Интеграция с Fantlab API

---

## Требования

```
python 3.9+
flask
python-telegram-bot >= 20.0   # только для бота
PyYAML                        # только для бота
```

Установка зависимостей:

```bash
pip install -r requirements.txt
```

---

## Использование

### 1. Конвертация INPX → SQLite

```bash
python3 inpx2sql.py -i mylib_fb2.inpx -o mylib_fb2.db
```

Скрипт читает все `.inp`-файлы из архива INPX и записывает книги в таблицу `books`.
ФИО авторов нормализуется из формата `Фамилия,Имя,Отчество:` → `Имя Отчество Фамилия`.
Жанры нормализуются из формата `sf_history:sf_action:` → `sf_history, sf_action`.

| Аргумент | Описание |
|---|---|
| `-i`, `--inpx` | Путь к INPX-файлу |
| `-o`, `--output` | Путь к выходному файлу базы данных |
| `-d`, `--debug` | Подробный вывод |

---

### 2. Веб-поисковик

```bash
python3 web-select.py \
  --database /path/to/mylib_fb2.db \
  --archives-path /path/to/archives/ \
  --host 0.0.0.0 \
  --port 5000
```

Параметры можно задавать переменными окружения: `DATABASE_PATH`, `ARCHIVES_PATH`, `HOST`, `PORT`.

| Аргумент | Переменная окружения | По умолчанию |
|---|---|---|
| `--database` | `DATABASE_PATH` | `books.db` |
| `--archives-path` | `ARCHIVES_PATH` | `archives` |
| `--host` | `HOST` | `127.0.0.1` |
| `--port` | `PORT` | `5000` |

**Возможности веб-версии:**
- Поиск без учёта регистра (включая кириллицу)
- Мультитокенный поиск: `Лазарчук Транквилиум` найдёт книги, где автор содержит «Лазарчук» **и** название содержит «Транквилиум»
- Сортировка по любому столбцу
- Скачивание книги
- Онлайн-чтение FB2 (кнопка «📖 Read»): регулировка шрифта, ночной режим, прогресс-бар
- Закладки: кнопка ⭐ сохраняет книгу в localStorage браузера

---

### 3. Telegram-бот

```bash
python3 bot.py --config config.yaml
```

Скопируйте пример конфигурации и заполните:

```bash
cp config.example.yaml config.yaml
$EDITOR config.yaml
```

```yaml
# config.yaml
token: "1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"   # токен от @BotFather

allowed_users:         # бот отвечает ТОЛЬКО этим ID; все остальные полностью игнорируются
  - 123456789

database_path: "/path/to/mylib_fb2.db"
archives_path: "/path/to/archives"
max_results: 5         # максимум результатов на запрос
```

**Использование бота:**
- `/start` — справка
- Любой текст → поиск. Поддерживает комбинации: `Толстой война`, `Лазарчук Транквилиум`
- Кнопка **⬇ Download** под результатом → бот присылает файл книги

> **Безопасность:** бот не отвечает и не реагирует никаким образом на сообщения пользователей, не входящих в `allowed_users`.

---

### 4. Docker

Образ запускает только веб-поисковик. Для бота используйте отдельный контейнер или `docker compose`.

```bash
# Сборка
docker build -t flib-web .

# Запуск
docker run -d -p 5000:5000 \
  -v /path/to/archives:/app/data/archives:ro \
  -v /path/to/mylib_fb2.db:/app/data/books.db:ro \
  --restart unless-stopped \
  --name flib-web \
  flib-web
```

Переменные окружения для кастомизации: `DATABASE_PATH`, `ARCHIVES_PATH`, `HOST`, `PORT`.

Пример `docker compose` для одновременного запуска веб-сервера и бота:

```yaml
# compose.yaml
services:
  web:
    build: .
    ports:
      - "5000:5000"
    volumes:
      - /path/to/archives:/app/data/archives:ro
      - /path/to/mylib_fb2.db:/app/data/books.db:ro
    restart: unless-stopped

  bot:
    build: .
    volumes:
      - /path/to/archives:/app/data/archives:ro
      - /path/to/mylib_fb2.db:/app/data/books.db:ro
      - ./config.yaml:/app/config.yaml:ro
    command: python bot.py --config /app/config.yaml
    restart: unless-stopped
```

---

## Структура репозитория

```
inpx2sql.py          — конвертер INPX → SQLite
web-select.py        — Flask веб-приложение
bot.py               — Telegram-бот
config.example.yaml  — пример конфигурации бота
Dockerfile           — образ для веб-поисковика
templates/
  results.html       — страница поиска с закладками
  reader.html        — страница онлайн-чтения FB2
```
