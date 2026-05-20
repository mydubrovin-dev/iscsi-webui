#!/bin/bash
set -e

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}=== iSCSI TGT WebUI Installer ===${NC}"

# Проверка прав
if [ "$EUID" -eq 0 ]; then
    echo -e "${RED}Не запускайте скрипт от root. Используйте обычного пользователя с sudo.${NC}"
    exit 1
fi

# Параметры по умолчанию
INSTALL_DIR="/opt/iscsi-tgt-webui"
USERNAME=$(whoami)
SERVICE_USER="www-data"
IMAGES_DIR="/var/lib/iscsi_images"
ADMIN_USER="admin"
ADMIN_PASS=$(openssl rand -base64 12 | tr -d '/+=' | cut -c1-16)
TGTADM_PATH=$(which tgtadm)
TGTADMIN_PATH=$(which tgt-admin)

if [ -z "$TGTADM_PATH" ]; then
    echo -e "${RED}Не найден tgtadm. Установите tgt (scsi-target-utils).${NC}"
    exit 1
fi
if [ -z "$TGTADMIN_PATH" ]; then
    echo -e "${RED}Не найден tgt-admin. Установите tgt (scsi-target-utils).${NC}"
    exit 1
fi

echo -e "${YELLOW}Будет установлено в: $INSTALL_DIR${NC}"
echo -e "${YELLOW}Пароль для admin: $ADMIN_PASS${NC} (сохраните его)"

# Создание директорий
sudo mkdir -p "$INSTALL_DIR" "$IMAGES_DIR"
sudo chown "$USERNAME":"$USERNAME" "$INSTALL_DIR"
sudo chown "$SERVICE_USER":"$SERVICE_USER" "$IMAGES_DIR"

# Копирование файлов (предполагается, что скрипт запущен из папки с исходниками)
cp -r . "$INSTALL_DIR/"

# Создание виртуального окружения
cd "$INSTALL_DIR"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

# Настройка .env файла
cat > "$INSTALL_DIR/.env" <<EOF
SECRET_KEY=$(openssl rand -hex 24)
ADMIN_USERNAME=$ADMIN_USER
ADMIN_PASSWORD=$ADMIN_PASS
IMAGES_DIR=$IMAGES_DIR
EOF

# Настройка sudoers для www-data
SUDOERS_FILE="/etc/sudoers.d/iscsi-webui"
sudo bash -c "cat > $SUDOERS_FILE" <<EOF
$SERVICE_USER ALL=(ALL) NOPASSWD: $TGTADM_PATH, $TGTADMIN_PATH
Defaults:$SERVICE_USER !requiretty
EOF
sudo chmod 440 "$SUDOERS_FILE"

# Установка systemd сервиса
cat > /tmp/iscsi-webui.service <<EOF
[Unit]
Description=iSCSI TGT Web UI (Gunicorn)
After=network.target

[Service]
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
Environment="PATH=$INSTALL_DIR/venv/bin"
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$INSTALL_DIR/venv/bin/gunicorn --workers 3 --bind 127.0.0.1:8000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
EOF
sudo mv /tmp/iscsi-webui.service /etc/systemd/system/iscsi-webui.service
sudo systemctl daemon-reload
sudo systemctl enable iscsi-webui
sudo systemctl start iscsi-webui

# Опционально: nginx
read -p "Настроить nginx (рекомендуется)? (y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    if ! command -v nginx &> /dev/null; then
        echo -e "${RED}nginx не установлен. Установите и запустите скрипт снова.${NC}"
    else
        SERVER_NAME=$(hostname -f)
        cat > /tmp/iscsi-webui-nginx.conf <<EOF
server {
    listen 80;
    server_name $SERVER_NAME;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
        sudo mv /tmp/iscsi-webui-nginx.conf /etc/nginx/sites-available/iscsi-webui
        sudo ln -sf /etc/nginx/sites-available/iscsi-webui /etc/nginx/sites-enabled/
        sudo nginx -t && sudo systemctl reload nginx
        echo -e "${GREEN}nginx настроен.${NC}"
    fi
fi

# Финальный вывод
echo -e "${GREEN}=== Установка завершена ===${NC}"
echo -e "Веб-интерфейс: http://$(hostname -I | awk '{print $1}')"
echo -e "Логин: $ADMIN_USER"
echo -e "Пароль: $ADMIN_PASS"
echo -e "Сохраните пароль! Его можно изменить в файле $INSTALL_DIR/.env"
echo -e "Статус сервиса: sudo systemctl status iscsi-webui"