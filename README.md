# flib-py

Комплекс скриптов для работы с inpx-библиотекой.

### Принцип работы (TODO)

- [x] Конвертация inpx в файл базы данных sqlite 
- [x] Поиск совпадений в базе sqlite
- [x] Выгрузка из архивов выбранной книги
- [ ] Работа через telegram
- [x] Dockerfile
- [-] Конвертация fb2 в другой целевой формат(txt, pdf, epub)
- [ ] Онлайн-чтение книги
- [ ] Интеграция с fantlab API

## Требования

```
python3
flask
```

## Использование

### Конвертирование inpx в базу sqlite

`python3 inpx2sql.py -i /mnt/data/www/documents/my_books/mylib_fb2.inpx -o /mnt/data/www/documents/my_books/mylib_fb2.db`

### Запуск web-поисковика по сгенерированной базе

`python3 web-select.py --database /mnt/data/www/documents/my_books/mylib_fb2.db --host 192.168.143.101 --archives-path /mnt/data/www/documents/my_books/`

### Запуск web-поисковика в контейнере

```
podman run -d -p 5000:5000 \
  -v /mnt/data/www/documents/my_books/:/app/data/archives \
  -v /mnt/data/www/documents/my_books/:/app/data/db \
  -e DATABASE_PATH=/app/data/db/mylib_fb2.db \
  --restart unless-stopped \
  --name flib-web \
  docker.io/dok2d/flib-web:0.7.1
```
