# AutoPrint (BarTender GUI)

Windows GUI для пакетной печати этикеток через BarTender. Интерфейс построен на `customtkinter`, используются шаблоны из каталога `templates` и справочник товаров из `product_catalog.xlsx` (или `Список товаров.xlsx`).

## Быстрый старт
- Установите Python 3.10+ на Windows и сам BarTender (для работы COM нужен установленный BarTender и пакет `pywin32`).
- Создайте окружение: `py -3 -m venv .venv` и активируйте его.
- Установите зависимости: `pip install -r requirements.txt`.
- Проверьте пути в `config/config.json` (шаблоны `.btw`, имя принтера, путь к каталогу товаров).
- Запустите приложение: `python bartender_gui.py`.

## Структура проекта
- `bartender_gui.py` — основной GUI, работа с шаблонами и данными.
- `data_processing.py` — загрузка CSV из Контур.Маркет и обогащение данными каталога.
- `bartender_com.py` — обертка над BarTender COM (pywin32).
- `templates/` — файлы шаблонов `.btw` и превью.
- `config/config.json`, `config/stats.json` — пользовательские настройки и статистика (проверьте перед публикацией).
- `build_tools/build_exe.py` — сборка standalone через PyInstaller: `python build_tools/build_exe.py`.

## Подготовка к публикации на GitHub
- Чувствительные данные. Просмотрите `config/` и Excel-файлы на предмет персональных данных или приватных путей; при необходимости добавьте их в `.gitignore` или замените тестовыми значениями.
- Минимальный набор файлов. Для работы нужны `.py`, `templates/*.btw`, `product_catalog.xlsx` (или `Список товаров.xlsx`) и базовые файлы в `config/`.
- Далее можно добавить удаленный репозиторий (`git remote add origin ...`), сделать коммит и выполнить `git push -u origin main`.
