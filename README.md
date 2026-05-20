# iSCSI TGT WebUI

Веб-интерфейс для управления iSCSI-таргетами (TGT) на Linux.

## Возможности

- Просмотр всех iSCSI-целей (Targets), LUN, ACL, CHAP-аккаунтов
- Активные сессии (подключения) – отображение инициатора и IP-адреса
- Создание/удаление целей, LUN, ACL
- Управление CHAP-аутентификацией (создание/удаление учёток, привязка к целям)
- Создание файлов-образов (толстые выделения) через dd
- Отображение свободного места на диске с образами
- Аутентификация администратора (одна учётка)
- Работа через Gunicorn + nginx (рекомендовано)

## Требования

- Linux-сервер с установленным **TGT** (`scsi-target-utils`)
- Python 3.8+
- sudo с правами для `tgtadm` и `tgt-admin`
- nginx (опционально, для production)

## Быстрая установка

1. Склонируйте репозиторий или распакуйте архив:
   ```bash
   cd /opt
   git clone https://github.com/your/iscsi-tgt-webui.git
   cd iscsi-tgt-webui
   Запустите установщик:

bash
chmod +x install.sh
./install.sh
Установщик создаст виртуальное окружение, настроит systemd, сгенерирует пароль, опционально настроит nginx.

Откройте в браузере http://ваш-сервер и войдите с выданным логином/паролем.

## Ручная установка
Если автоматический установщик не подходит, выполните шаги вручную:

1. Установите зависимости ОС
bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip gunicorn nginx tgt
2. Подготовьте директорию
bash
sudo mkdir -p /opt/iscsi-tgt-webui /var/lib/iscsi_images
sudo chown -R $USER:$USER /opt/iscsi-tgt-webui
sudo chown -R www-data:www-data /var/lib/iscsi_images
cd /opt/iscsi-tgt-webui
3. Скопируйте файлы проекта
Разместите в /opt/iscsi-tgt-webui:

app.py

config.py

requirements.txt

папку templates/

4. Настройте Python окружение
bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
deactivate
5. Создайте файл .env
bash
cat > .env <<EOF
SECRET_KEY=сгенерируйте-случайную-строку
ADMIN_USERNAME=admin
ADMIN_PASSWORD=ваш-надежный-пароль
IMAGES_DIR=/var/lib/iscsi_images
EOF
6. Настройте sudoers
bash
sudo visudo -f /etc/sudoers.d/iscsi-webui
# Вставьте:
www-data ALL=(ALL) NOPASSWD: /usr/sbin/tgtadm, /usr/sbin/tgt-admin
Defaults:www-data !requiretty
7. Установите systemd сервис
Создайте /etc/systemd/system/iscsi-webui.service со следующим содержимым:

ini
[Unit]
Description=iSCSI TGT Web UI (Gunicorn)
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/iscsi-tgt-webui
Environment="PATH=/opt/iscsi-tgt-webui/venv/bin"
EnvironmentFile=/opt/iscsi-tgt-webui/.env
ExecStart=/opt/iscsi-tgt-webui/venv/bin/gunicorn --workers 3 --bind 127.0.0.1:8000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
Затем:

bash
sudo systemctl daemon-reload
sudo systemctl enable --now iscsi-webui
8. Настройте nginx (рекомендуется)
nginx
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
Активируйте: sudo ln -s /etc/nginx/sites-available/iscsi-webui /etc/nginx/sites-enabled/ && sudo nginx -t && sudo systemctl reload nginx

Использование
Главная страница: список целей, свободное место, создание новых целей.

Страница цели: LUN, активные сессии, ACL, CHAP.

Управление образами: создание/удаление файлов-образов (в папке IMAGES_DIR).

CHAP учётки: глобальное управление, привязка к целям.

Проверка работы
После запуска откройте http://ваш-сервер. Попробуйте:

Создать файл-образ (например, 100 МБ).

Создать цель и привязать LUN.

Подключиться с другого хоста через iscsiadm.

Устранение неполадок
Ошибка 500 – проверьте логи: sudo journalctl -u iscsi-web-ui -n 50

Нет сессий – убедитесь, что tgt-admin -s выдаёт информацию. Если нет – возможно, не настроен вывод сессий в конфигурации TGT.

Не создаются LUN – проверьте права на файл/устройство.

CHAP не работает – проверьте привязку к цели и правильность пароля на инициаторе.

Лицензия
MIT (свободное использование).

text

---

## 📦 Файл `requirements.txt` (обновлённый)
Flask==2.3.2
Flask-Login==0.6.2
python-dotenv==1.0.0
psutil==5.9.5
gunicorn==20.1.0
setuptools<81

text

---

## 📄 `config.py` (без изменений, но для полноты)

```python
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'default-secret-key-change-me'
    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME') or 'admin'
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD') or 'admin123'
    IMAGES_DIR = os.environ.get('IMAGES_DIR') or '/var/lib/iscsi_images'
    TGTADM_CMD = '/usr/sbin/tgtadm'
🚀 Использование установщика
Распакуйте архив с проектом на сервер.

Перейдите в папку и выполните:

bash
chmod +x install.sh
sudo ./install.sh
При запросе настройки nginx ответьте y, если хотите.

После установки будет выведен сгенерированный пароль для admin.