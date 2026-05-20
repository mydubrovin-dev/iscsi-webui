#!/bin/bash
# Для тестирования без nginx/gunicorn
export FLASK_APP=app.py
export FLASK_ENV=production
# Установите свои переменные или используйте .env
export ADMIN_USERNAME=admin
export ADMIN_PASSWORD=admin123
export IMAGES_DIR=/var/lib/iscsi_images
sudo mkdir -p $IMAGES_DIR
sudo chown $USER:$USER $IMAGES_DIR
pip3 install -r requirements.txt
python3 app.py