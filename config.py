import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'hard-to-guess-string-change-in-production'
    # Пользователь для аутентификации (лучше задать через переменные окружения)
    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME') or 'admin'
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD') or 'admin123'
    # Путь к директории для образов (по умолчанию /var/lib/iscsi_images)
    IMAGES_DIR = os.environ.get('IMAGES_DIR') or '/var/lib/iscsi_images'
    # TGT commands
    TGTADM_CMD = '/usr/sbin/tgtadm'