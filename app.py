#!/usr/bin/env python3
import subprocess
import re
from flask import Flask, render_template, request, redirect, url_for, flash

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-in-production'  # для flash-сообщений

# ---- Вспомогательные функции для работы с tgtadm ----
def run_tgtadm_cmd(args, check=True):
    """Выполняет команду tgtadm и возвращает (stdout, stderr, returncode)"""
    cmd = ['sudo', 'tgtadm'] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        app.logger.error(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
    return result.stdout, result.stderr, result.returncode

def get_targets():
    """Парсит вывод tgtadm --lld iscsi --op show --mode target"""
    out, _, _ = run_tgtadm_cmd(['--lld', 'iscsi', '--op', 'show', '--mode', 'target'])
    targets = []
    current_target = None
    lines = out.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # Начало цели: Target 1: iqn.2023-04.local.myserver:disk1
        if line.startswith('Target '):
            if current_target:
                targets.append(current_target)
            parts = line.split(':')
            target_id = parts[0].split()[1]
            target_name = parts[1].strip()
            current_target = {
                'id': target_id,
                'name': target_name,
                'luns': [],
                'acls': [],
                'accounts': []
            }
        # LUN-ы:     LUN: 0
        elif line.startswith('LUN:') and current_target is not None:
            lun_num = line.split()[1]
            # следующая строка — тип (Type: disk)
            if i+1 < len(lines):
                type_line = lines[i+1].strip()
                if type_line.startswith('Type:'):
                    lun_type = type_line.split()[1]
                else:
                    lun_type = 'unknown'
                # следующая строка — путь (Backing store path: /dev/sdb1)
                if i+2 < len(lines):
                    path_line = lines[i+2].strip()
                    if path_line.startswith('Backing store path:'):
                        path = path_line.split(':', 1)[1].strip()
                    else:
                        path = ''
                else:
                    path = ''
                current_target['luns'].append({
                    'num': lun_num,
                    'type': lun_type,
                    'path': path
                })
        # ACL (разрешённые инициаторы):     ACL: iqn.1993-08.org.debian:01:123
        elif line.startswith('ACL:') and current_target is not None:
            acl = line.split(':', 1)[1].strip()
            current_target['acls'].append(acl)
        # Учётные записи CHAP (Outgoing)
        elif re.match(r'^\s+Outgoing\s+ACL:\s+', line) and current_target is not None:
            # обычно просто признак, что CHAP включён
            pass
        i += 1
    if current_target:
        targets.append(current_target)
    return targets

def parse_lun_show(out):
    """Дополнительный парсинг LUN, если нужен более точный вывод, но пока оставим"""
    return out

# ---- Маршруты веб-интерфейса ----
@app.route('/')
def index():
    targets = get_targets()
    return render_template('index.html', targets=targets)

@app.route('/add_target', methods=['POST'])
def add_target():
    name = request.form.get('name')
    if not name:
        flash('Не указано имя цели (IQN)', 'error')
        return redirect(url_for('index'))
    # tgtadm --lld iscsi --op new --mode target --tid=1 --targetname iqn...
    # нужно найти следующий свободный tid
    targets = get_targets()
    next_tid = 1
    existing_ids = [int(t['id']) for t in targets]
    while next_tid in existing_ids:
        next_tid += 1
    stdout, stderr, rc = run_tgtadm_cmd([
        '--lld', 'iscsi', '--op', 'new', '--mode', 'target',
        f'--tid={next_tid}', f'--targetname={name}'
    ])
    if rc == 0:
        flash(f'Цель {name} (TID {next_tid}) добавлена', 'success')
    else:
        flash(f'Ошибка: {stderr}', 'error')
    return redirect(url_for('index'))

@app.route('/delete_target/<tid>')
def delete_target(tid):
    # сначала нужно найти имя цели по tid, чтобы удалить
    targets = get_targets()
    target = next((t for t in targets if t['id'] == tid), None)
    if not target:
        flash(f'Цель с TID {tid} не найдена', 'error')
        return redirect(url_for('index'))
    name = target['name']
    stdout, stderr, rc = run_tgtadm_cmd([
        '--lld', 'iscsi', '--op', 'delete', '--mode', 'target',
        f'--tid={tid}'
    ])
    if rc == 0:
        flash(f'Цель {name} удалена', 'success')
    else:
        flash(f'Ошибка: {stderr}', 'error')
    return redirect(url_for('index'))

@app.route('/add_lun/<tid>', methods=['POST'])
def add_lun(tid):
    path = request.form.get('path')
    if not path:
        flash('Не указан путь к блочному устройству или файлу', 'error')
        return redirect(url_for('index'))
    # проверим, существует ли файл/блок
    try:
        subprocess.run(['test', '-e', path], check=True)
    except subprocess.CalledProcessError:
        flash(f'Путь {path} не существует', 'error')
        return redirect(url_for('index'))
    # tgtadm --lld iscsi --op new --mode logicalunit --tid=1 --lun=1 -b /dev/sdb1
    targets = get_targets()
    target = next((t for t in targets if t['id'] == tid), None)
    if not target:
        flash(f'Цель TID {tid} не найдена', 'error')
        return redirect(url_for('index'))
    # определим следующий свободный LUN (начиная с 1, т.к. LUN 0 обычно уже есть)
    used_luns = [int(l['num']) for l in target['luns']]
    next_lun = 1
    while next_lun in used_luns:
        next_lun += 1
    stdout, stderr, rc = run_tgtadm_cmd([
        '--lld', 'iscsi', '--op', 'new', '--mode', 'logicalunit',
        f'--tid={tid}', f'--lun={next_lun}', f'--backing-store={path}'
    ])
    if rc == 0:
        flash(f'LUN {next_lun} добавлен к цели {target["name"]}', 'success')
    else:
        flash(f'Ошибка: {stderr}', 'error')
    return redirect(url_for('index'))

@app.route('/delete_lun/<tid>/<lun_num>')
def delete_lun(tid, lun_num):
    stdout, stderr, rc = run_tgtadm_cmd([
        '--lld', 'iscsi', '--op', 'delete', '--mode', 'logicalunit',
        f'--tid={tid}', f'--lun={lun_num}'
    ])
    if rc == 0:
        flash(f'LUN {lun_num} удалён', 'success')
    else:
        flash(f'Ошибка: {stderr}', 'error')
    return redirect(url_for('index'))

@app.route('/add_acl/<tid>', methods=['POST'])
def add_acl(tid):
    initiator = request.form.get('initiator')
    if not initiator:
        flash('Не указан IQN инициатора', 'error')
        return redirect(url_for('index'))
    stdout, stderr, rc = run_tgtadm_cmd([
        '--lld', 'iscsi', '--op', 'bind', '--mode', 'target',
        f'--tid={tid}', f'--initiator-address={initiator}'
    ])
    if rc == 0:
        flash(f'Инициатор {initiator} добавлен в ACL цели TID {tid}', 'success')
    else:
        flash(f'Ошибка: {stderr}', 'error')
    return redirect(url_for('index'))

@app.route('/delete_acl/<tid>/<initiator>')
def delete_acl(tid, initiator):
    # initiator передаётся как URL-encoded, поэтому надо декодировать
    from urllib.parse import unquote
    initiator = unquote(initiator)
    stdout, stderr, rc = run_tgtadm_cmd([
        '--lld', 'iscsi', '--op', 'unbind', '--mode', 'target',
        f'--tid={tid}', f'--initiator-address={initiator}'
    ])
    if rc == 0:
        flash(f'Инициатор {initiator} удалён из ACL', 'success')
    else:
        flash(f'Ошибка: {stderr}', 'error')
    return redirect(url_for('index'))

if __name__ == '__main__':
    # Для production используйте gunicorn + nginx, а не встроенный сервер Flask
    app.run(host='0.0.0.0', port=5000, debug=False)