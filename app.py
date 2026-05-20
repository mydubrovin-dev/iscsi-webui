#!/usr/bin/env python3
import os
import subprocess
import re
import shutil
import socket
from pathlib import Path
from urllib.parse import unquote

from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required

from config import Config

app = Flask(__name__)
app.config.from_object(Config)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, id):
        self.id = id

@login_manager.user_loader
def load_user(user_id):
    if user_id == Config.ADMIN_USERNAME:
        return User(user_id)
    return None

def get_server_ips():
    """Возвращает список всех IPv4-адресов сервера (кроме localhost)"""
    try:
        result = subprocess.run(['hostname', '-I'], capture_output=True, text=True, check=True)
        ips = result.stdout.strip().split()
        # отфильтруем localhost (127.0.0.1) если он попал
        return [ip for ip in ips if not ip.startswith('127.')]
    except:
        return []

# ------------------- Утилиты для работы с tgt-admin -------------------
def run_tgtadmin(args, check=True):
    """Выполняет tgt-admin через sudo, возвращает (stdout, stderr, returncode)"""
    cmd = ['/usr/bin/sudo', 'tgt-admin'] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        app.logger.error(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
    return result.stdout, result.stderr, result.returncode

def parse_targets():
    """
    Парсит вывод tgt-admin -s
    Возвращает список целей с LUN, ACL, CHAP-аккаунтами, сессиями.
    """
    out, _, _ = run_tgtadmin(['-s'])
    targets = []
    current_target = None
    current_lun = None
    current_session = None
    lines = out.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        
        # Начало цели: "Target 1: iqn.2026-05.ru.ite-ng:ite205"
        if line.startswith('Target '):
            if current_target:
                targets.append(current_target)
            # Разбираем: "Target 1: iqn.2026-05.ru.ite-ng:ite205"
            parts = line.split(':', 1)
            tid_name = parts[0].split()
            tid = tid_name[1]
            name = parts[1].strip() if len(parts) > 1 else ""
            current_target = {
                'tid': tid,
                'name': name,
                'luns': [],
                'sessions': [],
                'acls': [],
                'accounts': []
            }
            current_lun = None
            current_session = None
        
        # Если цель ещё не начата, пропускаем
        elif current_target is None:
            i += 1
            continue
        
        # Системная информация (не нужна для отображения)
        elif line.startswith('System information:'):
            pass
        
        # I_T nexus information (сессии и подключения)
        elif line.startswith('I_T nexus information:'):
            pass
        
        elif line.startswith('I_T nexus:'):
            # Начинаем новую сессию
            # Пример: "I_T nexus: 12"
            nexus_id = line.split(':')[1].strip()
            current_session = {
                'id': nexus_id,
                'initiator': '',
                'ip': '',
                'luns': []
            }
            if current_target:
                current_target['sessions'].append(current_session)
        
        elif line.startswith('Initiator:') and current_session:
            # "Initiator: iqn.2026-05.ru.ite-ng:ite203 alias: none"
            init_str = line.split(':', 1)[1].strip()
            # отделяем alias
            if ' alias:' in init_str:
                initiator = init_str.split(' alias:')[0]
            else:
                initiator = init_str
            current_session['initiator'] = initiator
        
        elif line.startswith('IP Address:') and current_session:
            ip = line.split(':', 1)[1].strip()
            current_session['ip'] = ip
        
        elif line.startswith('LUN information:'):
            pass
        
        elif line.startswith('LUN:') and not line.startswith('LUN information:'):
            # Начинаем новый LUN
            lun_num = line.split(':')[1].strip()
            current_lun = {
                'num': lun_num,
                'type': '',
                'backing_store': '',
                'size': ''
            }
            if current_target:
                current_target['luns'].append(current_lun)
        
        # Поля внутри LUN
        elif line.startswith('Type:') and current_lun:
            current_lun['type'] = line.split(':', 1)[1].strip()
        elif line.startswith('Backing store path:') and current_lun:
            path = line.split(':', 1)[1].strip()
            if path != 'None':
                current_lun['backing_store'] = path
        elif line.startswith('Size:') and current_lun:
            size_str = line.split(':', 1)[1].strip()
            # Пример: "Size: 32212 MB"
            current_lun['size'] = size_str
        
        # Account information
        elif line.startswith('Account information:'):
            pass
        elif current_target and (line.startswith('Account:') or (not line.startswith('ACL') and current_target and 'Account information' in lines[max(0,i-1)])):
            # В выводе после "Account information:" идут имена аккаунтов без маркера "Account:"? 
            # По примеру: строка "        backupadmin"
            if line and not line.startswith('ACL') and not line.startswith('I_T nexus') and not line.startswith('LUN'):
                # Просто имя аккаунта
                acc = line.strip()
                if acc and acc not in current_target['accounts']:
                    current_target['accounts'].append(acc)
        
        # ACL information
        elif line.startswith('ACL information:'):
            pass
        elif line.startswith('ACL:') and current_target:
            acl = line.split(':', 1)[1].strip()
            if acl not in current_target['acls']:
                current_target['acls'].append(acl)
        elif current_target and line and not line.startswith('Target') and not line.startswith('System') and not line.startswith('I_T') and not line.startswith('LUN') and not line.startswith('Account') and not line.startswith('ACL') and not line.startswith('Initiator') and not line.startswith('IP'):
            # В некоторых версиях ACL может быть просто строка "ALL" без префикса "ACL:"
            if line == 'ALL' and 'ALL' not in current_target['acls']:
                current_target['acls'].append('ALL')
        
        i += 1
    
    if current_target:
        targets.append(current_target)
    
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

# ------------------- Работа с CHAP (через tgtadm, так как tgt-admin не умеет управлять) -------------------
def run_tgtadm(args, check=True):
    cmd = ['/usr/bin/sudo', Config.TGTADM_CMD] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        app.logger.error(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
    return result.stdout, result.stderr, result.returncode

def get_all_chap_accounts():
    out, _, _ = run_tgtadm(['--lld', 'iscsi', '--op', 'show', '--mode', 'account'])
    accounts = []
    lines = out.splitlines()
    for line in lines:
        line = line.strip()
        if line and not line.startswith('Account list:'):
            # Убираем возможные префиксы "Account:" если есть
            if line.startswith('Account:'):
                acc = line.split(':', 1)[1].strip()
            else:
                acc = line
            if acc:
                accounts.append(acc)
    return accounts
    
def add_chap_account(user, password):
    _, stderr, rc = run_tgtadm(['--lld', 'iscsi', '--op', 'new', '--mode', 'account',
                                '--user', user, '--password', password])
    return rc == 0, stderr

def delete_chap_account(user):
    _, stderr, rc = run_tgtadm(['--lld', 'iscsi', '--op', 'delete', '--mode', 'account',
                                '--user', user])
    return rc == 0, stderr

def bind_chap_to_target(tid, user):
    _, stderr, rc = run_tgtadm(['--lld', 'iscsi', '--op', 'bind', '--mode', 'account',
                                f'--tid={tid}', '--user', user])
    return rc == 0, stderr

def unbind_chap_from_target(tid, user):
    _, stderr, rc = run_tgtadm(['--lld', 'iscsi', '--op', 'unbind', '--mode', 'account',
                                f'--tid={tid}', '--user', user])
    return rc == 0, stderr

# ------------------- Работа с файлами-образами -------------------
def get_images_info():
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
    images_dir = Path(Config.IMAGES_DIR)
    images_dir.mkdir(exist_ok=True)
    path = images_dir / name
    if path.exists():
        return False, "Файл уже существует"
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
    usage = shutil.disk_usage(Config.IMAGES_DIR)
    return {
        'total': usage.total,
        'used': usage.used,
        'free': usage.free,
        'percent': (usage.used / usage.total) * 100,
        'path': Config.IMAGES_DIR
    }

# ------------------- Маршруты -------------------
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
    server_ips = get_server_ips()   # <-- добавить
    return render_template('index.html', targets=targets, disk_usage=disk_usage, images=images, server_ips=server_ips)

@app.route('/target/<tid>')
@login_required
def target_detail(tid):
    targets = parse_targets()
    target = next((t for t in targets if t['tid'] == tid), None)
    if not target:
        flash('Цель не найдена', 'danger')
        return redirect(url_for('index'))
    images = get_images_info()
    all_chap_accounts = get_all_chap_accounts()
    #app.logger.error(f"CHAP accounts: {all_chap_accounts}")  # отладка
    return render_template('target_detail.html', target=target, images=images, all_chap_accounts=all_chap_accounts)

@app.route('/add_target', methods=['POST'])
@login_required
def add_target():
    name = request.form.get('name')
    if not name:
        flash('IQN не может быть пустым', 'danger')
        return redirect(url_for('index'))
    tid = get_next_tid()
    # используем tgtadm для создания, так как tgt-admin в основном для конфигов
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
    path_custom = request.form.get('path_custom')
    if path_custom:
        path = path_custom
    if not path:
        flash('Путь не указан', 'danger')
        return redirect(url_for('target_detail', tid=tid))
    if not os.path.exists(path):
        flash(f'Путь {path} не существует', 'danger')
        return redirect(url_for('target_detail', tid=tid))
    lun = get_next_lun(int(tid))
    _, stderr, rc = run_tgtadm(['--lld', 'iscsi', '--op', 'new', '--mode', 'logicalunit', f'--tid={tid}', f'--lun={lun}', f'--backing-store={path}'])
    if rc == 0:
        flash(f'LUN {lun} добавлен', 'success')
    else:
        flash(f'Ошибка: {stderr}', 'danger')
    return redirect(url_for('target_detail', tid=tid))

@app.route('/delete_lun/<tid>/<lun_num>')
@login_required
def delete_lun(tid, lun_num):
    if lun_num == '0':
        flash('LUN 0 (контроллер) нельзя удалить', 'danger')
        return redirect(url_for('target_detail', tid=tid))
    _, stderr, rc = run_tgtadm(['--lld', 'iscsi', '--op', 'delete', '--mode', 'logicalunit', f'--tid={tid}', f'--lun={lun_num}'])
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
    initiator = unquote(initiator)
    _, stderr, rc = run_tgtadm(['--lld', 'iscsi', '--op', 'unbind', '--mode', 'target',
                                f'--tid={tid}', f'--initiator-address={initiator}'])
    if rc == 0:
        flash(f'Инициатор {initiator} удалён из ACL', 'success')
    else:
        flash(f'Ошибка: {stderr}', 'danger')
    return redirect(url_for('target_detail', tid=tid))

# CHAP на уровне цели
@app.route('/add_chap_to_target/<tid>', methods=['POST'])
@login_required
def add_chap_to_target(tid):
    user = request.form.get('chap_user')
    if not user:
        flash('Не выбран CHAP пользователь', 'danger')
        return redirect(url_for('target_detail', tid=tid))
    all_acc = get_all_chap_accounts()
    if user not in all_acc:
        flash(f'Учётка {user} не существует. Создайте её на странице CHAP.', 'danger')
        return redirect(url_for('target_detail', tid=tid))
    ok, err = bind_chap_to_target(tid, user)
    if ok:
        flash(f'CHAP пользователь {user} привязан к цели TID {tid}', 'success')
    else:
        flash(f'Ошибка привязки CHAP: {err}', 'danger')
    return redirect(url_for('target_detail', tid=tid))

@app.route('/remove_chap_from_target/<tid>/<user>')
@login_required
def remove_chap_from_target(tid, user):
    ok, err = unbind_chap_from_target(tid, user)
    if ok:
        flash(f'CHAP пользователь {user} отвязан от цели TID {tid}', 'success')
    else:
        flash(f'Ошибка отвязки CHAP: {err}', 'danger')
    return redirect(url_for('target_detail', tid=tid))

@app.route('/chap_accounts')
@login_required
def chap_accounts():
    accounts = get_all_chap_accounts()
    return render_template('chap_accounts.html', accounts=accounts)

@app.route('/add_chap_account', methods=['POST'])
@login_required
def add_chap_account_route():
    user = request.form.get('user')
    password = request.form.get('password')
    if not user or not password:
        flash('Логин и пароль обязательны', 'danger')
        return redirect(url_for('chap_accounts'))
    ok, err = add_chap_account(user, password)
    if ok:
        flash(f'CHAP учётка {user} создана', 'success')
    else:
        flash(f'Ошибка: {err}', 'danger')
    return redirect(url_for('chap_accounts'))

@app.route('/delete_chap_account/<user>')
@login_required
def delete_chap_account_route(user):
    ok, err = delete_chap_account(user)
    if ok:
        flash(f'CHAP учётка {user} удалена', 'success')
    else:
        flash(f'Ошибка: {err}', 'danger')
    return redirect(url_for('chap_accounts'))

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
    app.run(host='0.0.0.0', port=5000, debug=False)