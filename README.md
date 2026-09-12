# AutoPrint

Windows GUI для пакетной печати этикеток через BarTender. Интерфейс построен на `customtkinter`, используются шаблоны из каталога `templates` и справочник товаров.

## Offline license

До создания основного окна приложение проверяет подписанный файл `config/license.json`. Machine code показывается в окне ошибки лицензии и строится из Windows MachineGuid без сохранения исходного значения.

Ключи и лицензии выпускаются отдельным приватным репозиторием `AutoPrint-License-Tools`. После генерации issuer key pair добавьте `public_key_base64` из публичного manifest в `PRODUCTION_PUBLIC_KEYS` в `licensing/signature.py`; private key никогда не копируется в этот репозиторий. Пустой production keyring намеренно блокирует все лицензии до этого шага.

