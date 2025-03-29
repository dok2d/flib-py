# flib-py

Комплекс скриптов для работы с inpx-библиотекой.

### Принцип работы (TODO)

- [x] Конвертация inpx в файл базы данных sqlite 
- [x] Поиск совпадений в базе sqlite
- [x] Выгрузка из архивов выбранной книги
- [ ] Работа через telegram
- [ ] Конвертация fb2 в другой целевой формат(txt, pdf, epub)

## Требования

```
python3
flask
```

## Запуск

```
python3 inpx2sql.py -i /mnt/data/www/documents/mylib_fb2.inpx -o /mnt/data/www/documents/mylib_fb2.db
python3 web-select.py --database /mnt/data/www/documents/output_db.db --host 192.168.143.101 --archives-path /mnt/data/www/documents
```
