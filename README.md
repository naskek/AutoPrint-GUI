# AutoPrint

Windows GUI для пакетной печати этикеток через BarTender. Интерфейс построен на `customtkinter`, используются шаблоны из каталога `templates` и справочник товаров.

## Шаблоны GS1 DataMatrix для «Честного знака»

Проверенная на физических этикетках 17.11.2026 инструкция по созданию шаблона коротких кодов 01/21/93 в BarTender 2022 R5: [настройка GS1 DataMatrix и проверка в «ЧЗ.Бизнес»](docs/gs1-datamatrix-chz-business-bartender.md).

**Важно:** корректность исходного CSV не доказывает корректность итогового Data Matrix, а новый однополевой BTW ещё требует отдельной проверки совместимости с предпросмотром и пакетной печатью AutoPrint ([Issue #94](https://github.com/naskek/AutoPrint-GUI/issues/94)).

## Offline license

До создания основного окна приложение проверяет подписанный файл `config/license.json`. Machine code показывается в окне ошибки лицензии и строится из Windows MachineGuid без сохранения исходного значения.

Ключи и лицензии выпускаются отдельным приватным репозиторием `AutoPrint-License-Tools`. После генерации issuer key pair добавьте `public_key_base64` из публичного manifest в `PRODUCTION_PUBLIC_KEYS` в `licensing/signature.py`; private key никогда не копируется в этот репозиторий. Пустой production keyring намеренно блокирует все лицензии до этого шага.

