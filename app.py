#!/usr/bin/env python3
import os
import subprocess
import re
import shutil
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, flash, abort
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import psutil

from config import Config

app = Flask(__name__)
app.config.from_object(Config)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Простая модель пользователя
class User(UserMixin):
    def __init__(self, id):
        self.id = id

@login_manager.user_loader
def load_user(user_id):
    if user_id == Config.ADMIN_USERNAME:
        return User(user_id)
    return None

# ------------------- Утилиты для работы с tgtadm -------------------
def run_tgtadm(args, check=True):
    """Выполняет tgtadm с sudo (предполагается настроенный sudo без пароля)"""
    cmd = ['sudo', Config.TGTADM_CMD] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        app.logger.error(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
    return result.stdout, result.stderr, result.returncode

def parse_targets():
    """Парсит вывод tgtadm --lld iscsi --op show --mode target"""
    out, _, _ = run_tgtadm(['--lld', 'iscsi', '--op', 'show', '--mode', 'target'])
    targets = []
    current = None
    lines = out.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith('Target '):
            if current:
                targets.append(current)
            parts = line.split(':')
            tid = parts[0].split()[1]
            name = parts[1].strip()
            current = {
                'tid': tid,
                'name': name,
                'luns': [],
                'acls': [],
                'accounts': []
            }
        elif line.startswith('LUN:') and current:
            lun_num = line.split()[1]
            # Следующие строки содержат информацию
            lun_type = ''
            backing_store = ''
            j = i+1
            while j < len(lines) and lines[j].strip():
                l = lines[j].strip()
                if l.startswith('Type:'):
                    lun_type = l.split()[1]
                elif l.startswith('Backing store path:'):
                    backing_store = l.split(':', 1)[1].strip()
                elif l.startswith('LUN:'):
                    break
                j += 1
            current['luns'].append({
                'num': lun_num,
                'type': lun_type,
                'backing_store': backing_store
            })
        elif line.startswith('ACL:') and current:
            acl = line.split(':', 1)[1].strip()
            current['acls'].append(acl)
        i += 1
    if current:
        targets.append(current)
    return targets

def get_next_tid():
    targets = parse_targets()
    existing = [int(t['tid']) for t in targets]
    tid = 1
    while tid in existing:
        tid += 1
    return tid

def get_next_lun(tid):
    targets = parse_targets()
    for t in targets:
        if t['tid'] == str(tid):
            used = [int(l['num']) for l in t['luns']]
            lun = 0
            while lun in used:
                lun += 1
            return lun
    return 0

# ------------------- Работа с файлами-образами -------------------
def get_images_info():
    """Возвращает список словарей с информацией о файлах-образах: имя, размер, путь"""
    images_dir = Path(Config.IMAGES_DIR)
    images_dir.mkdir(exist_ok=True)
    images = []
    for f in images_dir.iterdir():
        if f.is_file():
            stat = f.stat()
            size_bytes = stat.st_size
            size_mb = size_bytes / (1024*1024)
            images.append({
                'name': f.name,
                'path': str(f),
                'size_bytes': size_bytes,
                'size_mb': round(size_mb, 2),
                'modified': stat.st_mtime
            })
    return images

def create_image(name, size_mb):
    """Создаёт файл-образ заданного размера (в МБ) с помощью dd"""
    images_dir = Path(Config.IMAGES_DIR)
    images_dir.mkdir(exist_ok=True)
    path = images_dir / name
    if path.exists():
        return False, "Файл уже существует"
    # Создаём файл с нулями
    try:
        subprocess.run(['dd', 'if=/dev/zero', f'of={path}', f'bs=1M', f'count={size_mb}'],
                       check=True, stderr=subprocess.PIPE)
        return True, f"Образ {name} создан, размер {size_mb} МБ"
    except subprocess.CalledProcessError as e:
        return False, f"Ошибка создания: {e.stderr.decode()}"

def delete_image(name):
    path = Path(Config.IMAGES_DIR) / name
    if path.exists():
        path.unlink()
        return True, "Образ удалён"
    return False, "Файл не найден"

def get_disk_usage():
    """Возвращает информацию о свободном месте на диске, где хранятся образы"""
    usage = shutil.disk_usage(Config.IMAGES_DIR)
    return {
        'total': usage.total,
        'used': usage.used,
        'free': usage.free,
        'percent': (usage.used / usage.total) * 100
    }

# ------------------- Маршруты веб-интерфейса -------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == Config.ADMIN_USERNAME and password == Config.ADMIN_PASSWORD:
            user = User(username)
            login_user(user)
            return redirect(url_for('index'))
        else:
            flash('Неверное имя пользователя или пароль', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    targets = parse_targets()
    disk_usage = get_disk_usage()
    images = get_images_info()
    return render_template('index.html', targets=targets, disk_usage=disk_usage, images=images)

@app.route('/target/<tid>')
@login_required
def target_detail(tid):
    targets = parse_targets()
    target = next((t for t in targets if t['tid'] == tid), None)
    if not target:
        flash('Цель не найдена', 'danger')
        return redirect(url_for('index'))
    images = get_images_info()
    return render_template('target_detail.html', target=target, images=images)

@app.route('/add_target', methods=['POST'])
@login_required
def add_target():
    name = request.form.get('name')
    if not name:
        flash('IQN не может быть пустым', 'danger')
        return redirect(url_for('index'))
    tid = get_next_tid()
    _, stderr, rc = run_tgtadm(['--lld', 'iscsi', '--op', 'new', '--mode', 'target',
                                f'--tid={tid}', f'--targetname={name}'])
    if rc == 0:
        flash(f'Цель {name} (TID {tid}) создана', 'success')
    else:
        flash(f'Ошибка: {stderr}', 'danger')
    return redirect(url_for('index'))

@app.route('/delete_target/<tid>')
@login_required
def delete_target(tid):
    _, stderr, rc = run_tgtadm(['--lld', 'iscsi', '--op', 'delete', '--mode', 'target', f'--tid={tid}'])
    if rc == 0:
        flash(f'Цель TID {tid} удалена', 'success')
    else:
        flash(f'Ошибка: {stderr}', 'danger')
    return redirect(url_for('index'))

@app.route('/add_lun/<tid>', methods=['POST'])
@login_required
def add_lun(tid):
    path = request.form.get('path')
    if not path:
        flash('Путь к устройству/файлу не указан', 'danger')
        return redirect(url_for('target_detail', tid=tid))
    # Проверяем существование файла
    if not os.path.exists(path):
        flash(f'Путь {path} не существует', 'danger')
        return redirect(url_for('target_detail', tid=tid))
    lun = get_next_lun(int(tid))
    _, stderr, rc = run_tgtadm(['--lld', 'iscsi', '--op', 'new', '--mode', 'logicalunit',
                                f'--tid={tid}', f'--lun={lun}', f'--backing-store={path}'])
    if rc == 0:
        flash(f'LUN {lun} добавлен (бэк-стор {path})', 'success')
    else:
        flash(f'Ошибка: {stderr}', 'danger')
    return redirect(url_for('target_detail', tid=tid))

@app.route('/delete_lun/<tid>/<lun_num>')
@login_required
def delete_lun(tid, lun_num):
    _, stderr, rc = run_tgtadm(['--lld', 'iscsi', '--op', 'delete', '--mode', 'logicalunit',
                                f'--tid={tid}', f'--lun={lun_num}'])
    if rc == 0:
        flash(f'LUN {lun_num} удалён', 'success')
    else:
        flash(f'Ошибка: {stderr}', 'danger')
    return redirect(url_for('target_detail', tid=tid))

@app.route('/add_acl/<tid>', methods=['POST'])
@login_required
def add_acl(tid):
    initiator = request.form.get('initiator')
    if not initiator:
        flash('IQN инициатора не указан', 'danger')
        return redirect(url_for('target_detail', tid=tid))
    _, stderr, rc = run_tgtadm(['--lld', 'iscsi', '--op', 'bind', '--mode', 'target',
                                f'--tid={tid}', f'--initiator-address={initiator}'])
    if rc == 0:
        flash(f'Инициатор {initiator} добавлен в ACL', 'success')
    else:
        flash(f'Ошибка: {stderr}', 'danger')
    return redirect(url_for('target_detail', tid=tid))

@app.route('/delete_acl/<tid>/<path:initiator>')
@login_required
def delete_acl(tid, initiator):
    from urllib.parse import unquote
    initiator = unquote(initiator)
    _, stderr, rc = run_tgtadm(['--lld', 'iscsi', '--op', 'unbind', '--mode', 'target',
                                f'--tid={tid}', f'--initiator-address={initiator}'])
    if rc == 0:
        flash(f'Инициатор {initiator} удалён из ACL', 'success')
    else:
        flash(f'Ошибка: {stderr}', 'danger')
    return redirect(url_for('target_detail', tid=tid))

# Управление образами
@app.route('/images')
@login_required
def images():
    images_list = get_images_info()
    disk_usage = get_disk_usage()
    return render_template('images.html', images=images_list, disk_usage=disk_usage)

@app.route('/create_image', methods=['POST'])
@login_required
def create_image_route():
    name = request.form.get('name')
    size = request.form.get('size_mb')
    if not name or not size:
        flash('Имя и размер обязательны', 'danger')
        return redirect(url_for('images'))
    if not name.endswith('.img'):
        name += '.img'
    try:
        size_mb = int(size)
    except ValueError:
        flash('Размер должен быть числом (МБ)', 'danger')
        return redirect(url_for('images'))
    success, msg = create_image(name, size_mb)
    if success:
        flash(msg, 'success')
    else:
        flash(msg, 'danger')
    return redirect(url_for('images'))

@app.route('/delete_image/<name>')
@login_required
def delete_image_route(name):
    success, msg = delete_image(name)
    if success:
        flash(msg, 'success')
    else:
        flash(msg, 'danger')
    return redirect(url_for('images'))

if __name__ == '__main__':
    # Для разработки, в production используйте gunicorn
    app.run(host='0.0.0.0', port=5000, debug=False)