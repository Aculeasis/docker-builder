#!/usr/bin/env python3

import os
import platform
import shutil
import subprocess
import sys
import threading
import time

import docker  # pip3 install docker
import requests  # pip3 install requests
from docker.errors import BuildError

# TODO: Обработка зависимостей, например 'depends'=[] список зависимостей типа индекс\имя, индекс\имя. Хз как лучше.
# TODO: Переписать работу с докером с subprocess на docker

DEF_TAGS = {        # Подстановки доступные в теге:
    'arch': '',     # Архитектура системы где запущен скрипт: amd64, arm64v8, arm32v7 или unknown
    'c_full': '',   # Хеш последнего комитта из git-репа, например dc361791f21a4132dcf4f385b69c4448fa9a48c1
    'c_short': '',  # Короткий вариант 'c_full', например dc36179
    'tag': '',      # Актуальный тег из git-репа, если нет вернет пустую строку. Например 0.7.1
    'tag_full': ''  # Полная версия 'tag', например 0.7.1-1-gdc36179
}                   # Примеры: '{arch}-{tag}' -> 'arm64v8-0.7.1', 'build_{c_short}' -> 'build_dc36179' и т.д.


class Build(threading.Thread):
    def __init__(self, tag, path, w_dir):
        super().__init__()
        self.tag = tag
        self._path = path
        self._w_dir = w_dir
        self._status = None
        self.err = ''
        self.work_time = 0
        self.start()

    def status(self):
        return self._status

    def run(self):
        client = docker.from_env()
        work_time = time.time()
        try:
            client.images.build(tag=self.tag, dockerfile=self._path, path=self._w_dir, rm=True, nocache=True)
        except (TypeError, BuildError)as e:
            self.err = str(e)
            _status = 1
        else:
            _status = 0
        self.work_time = int(time.time() - work_time)
        self._status = _status


class Push(threading.Thread):
    def __init__(self, tag):
        super().__init__()
        self.tag = tag
        self._repository, self._tag = tag.rsplit(':', 1)
        self._status = None
        self.err = ''
        self.work_time = 0
        self.start()

    def status(self):
        return self._status

    def run(self):
        client = docker.from_env()
        work_time = time.time()
        _status = None
        for retry in range(1, 5):
            try:
                self.err = client.images.push(repository=self._repository, tag=self._tag)
            except requests.exceptions.ConnectionError as e:
                print('Error push {}:{}. Retry {}. MSG: {}'.format(self._repository, self._tag, retry, e))
            else:
                _status = 0
                break
        if _status is None:
            self.err += ' socket error'
            _status = 1
        self.work_time = int(time.time() - work_time)
        self._status = _status


def _get_arch() -> str:
    aarch = {'x86_64': 'amd64', 'amd64': 'amd64', 'aarch64': 'arm64v8', 'armv7l': 'arm32v7'}
    return aarch.get(platform.uname()[4].lower(), 'unknown')


def _get_arch_from_dockerfile(path) -> str:
    # вернет архитектуру, если найдет ее во FROM. Или ''
    if not os.path.isfile(path):
        raise RuntimeError('File not found {}'.format(path))
    aarch = ['amd64', 'arm64v8', 'arm32v7']
    with open(path, encoding='utf8') as f:
        for l in f.readlines():
            if l.startswith('FROM'):
                for test in aarch:
                    if test in l.lower():
                        return test
                return ''
    return ''


def _is_git(path) -> bool:
    # гит или не гит
    run = subprocess.run(['git', '-C', path, 'status'], stderr=subprocess.PIPE, stdout=subprocess.PIPE)
    return not run.returncode


def _git_clone(url, path) -> bool:
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    run = subprocess.run(['git', 'clone', url, path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if run.returncode:
        print('Clone error {} to {}: {}'.format(url, path, run.stderr.decode()))
        return False
    return True


def _git_pull(path) -> list:
    # вернет список изменившихся файлов
    old_hash = _git_get_full_hash(path)
    run = subprocess.run(['git', '-C', path, 'pull'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    new_hash = _git_get_full_hash(path)
    if run.returncode:
        print('Pull error {}'.format(path))
        print(run.stderr.decode())
        return []
    if old_hash == new_hash or not len(old_hash) or not len(new_hash):
        return []
    run = subprocess.run(
        ['git', '-C', path, 'diff', '-z', '--name-only', old_hash, new_hash],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    if not run.returncode:
        return run.stdout.decode().strip('\n\0').split('\0')
    return []


def _git_get_full_hash(path) -> str:
    run = subprocess.run(['git', '-C', path, 'log', '-n', '1'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if not run.returncode:
        # commit 2a2f3f60c7bc168c3121c07fefc84bedf9ed4abd\n -> 2a2f3f60c7bc168c3121c07fefc84bedf9ed4abd or ''
        return run.stdout.decode().split('\n')[0].split(' ')[-1]
    return ''


def _git_get_tags(path: str, cfg: dict) -> dict:
    # вернет заполненные теги
    def get_git_str(cmd_: list) -> str:
        cmd = ['git', '-C', path]
        run = subprocess.run(cmd + cmd_, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return run.stdout.decode().strip('\n') if not run.returncode else ''

    tag_c_full = _git_get_full_hash(path)
    ready = {
        # arm64v8
        'arch': cfg['arch'],
        # 2a2f3f60c7bc168c3121c07fefc84bedf9ed4abd
        'c_full': tag_c_full,
        # 2a2f3f60c7bc168c3121c07fefc84bedf9ed4abd -> 2a2f3f6
        'c_short': get_git_str(['rev-parse', '--short=7', tag_c_full]),
        # 0.7.1-1-gdc36179
        'tag_full': get_git_str(['describe', ]),
        # 0.7.1
        'tag': get_git_str(['describe', '--abbrev=0'])
    }
    tags = DEF_TAGS.copy()
    for key, value in ready.items():
        if value and key in tags:
            tags[key] = value
    return tags


def __docker_run_fatal(cmd2: list, fatal: bool = True):
    run = subprocess.run(['docker', ] + cmd2, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
    if run.returncode:
        if fatal:
            raise RuntimeError('Error docker {}: {}'.format(' '.join(cmd2), run.stderr.decode()))
        else:
            print('Error docker {}: {}'.format(' '.join(cmd2), run.stderr.decode()))
    return run


def _docker_login(cfg):
    # Читаем логин пасс из файла и логинимся.
    with open(cfg['credentials'], encoding='utf8') as f:
        data = [l.strip() for l in f.readline().strip('\n').split(' ', 1)]
    if len(data) != 2:
        raise RuntimeError('Bad credentials, file {}, len={}!=2'.format(cfg['credentials'], len(data)))
    run = subprocess.run(
        ['docker', 'login', '-u', data[0], '-p', data[1]],
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE
    )
    cfg['user'] = data[0]
    if run.returncode:
        raise RuntimeError('Error docker login: {}'.format(run.stderr.decode()))


def docker_logout():
    __docker_run_fatal(['logout', ])


def _docker_containers():
    # вернет список из [ид образа, имя контейнера], ps -a --format "{{.Image}} {{.Names}}"
    run = __docker_run_fatal(['ps', '-a', '--format', '{{.Image}} {{.Names}}'])
    return [line.split(' ', 1) for line in run.stdout.decode().split('\n') if len(line) > 2]


def _docker_images():
    # Вернет список из [ид образа, реп:тег], docker images --format "{{.ID}} {{.Repository}}:{{.Tag}}
    run = __docker_run_fatal(['images', '--format', '{{.ID}} {{.Repository}}:{{.Tag}}'])
    return [line.split(' ', 1) for line in run.stdout.decode().split('\n') if len(line) > 2]


def _docker_prune_container(name: str):
    # Остановит и удалит контейнер
    __docker_run_fatal(['stop', name])
    __docker_run_fatal(['rm', name])


def docker_prune_image(name: str, fatal: bool = True):
    # Удалит образ по реп:тег
    __docker_run_fatal(['rmi', name], fatal)


def docker_system_prune(fatal: bool = False):
    # docker system prune -f
    __docker_run_fatal(['system', 'prune', '-f'], fatal)


def _cfg_prepare(cfg: dict):
    if not os.path.isdir(cfg['work_dir']):
        raise RuntimeError('\'work_dir\' not found: {}', cfg['work_dir'])
    triggers = os.path.join(cfg['work_dir'], cfg['triggers'])
    if not os.path.isdir(triggers):
        os.mkdir(triggers)
    if cfg['auto_push']:
        cfg['credentials'] = cfg['credentials'] if cfg['credentials'].startswith('/') \
            else os.path.join(cfg['work_dir'], cfg['credentials'])
    elif cfg['remove_after_push']:
        cfg['remove_after_push'] = False
        print('\'auto_push\' is False. Set \'remove_after_push\' to False.')
    cfg['arch'] = _get_arch()
    if cfg['arch_detect'] and cfg['arch'] == 'unknown':
        raise RuntimeError('Unknown architecture and \'arch_detect\' is True')
    return cfg


class GenerateBuilds:
    def __init__(self, cfg, targets_all, git_triggers, args):
        self._cli = args
        self.cfg = _cfg_prepare(cfg)
        self.targets_all = targets_all
        self.git_triggers = git_triggers
        self.filled_triggers = {}
        self.to_build = []
        self.all_build_name = set()
        # Не дублировать репозитории. Если реп уже обновлен используем его и его данные для других. Уникальный id - url
        # Формат 'url': {dir: dir, files: [] or None}
        self.known_repos = {}
        if self._cli.v:
            print('Architecture: {}'.format(self.cfg['arch']))

    def get(self):
        self._generate()
        return_me = []
        print_allow = []
        print_ignore = []
        for i in self.to_build:
            if i['true']:
                print_allow.append('Allow building {} from {}: {}'.format(i['cmd'][0], i['cmd'][1], i['reason']))
                # Дополняем пути
                i['cmd'][1] = os.path.join(self.cfg['work_dir'], i['cmd'][1])
                i['cmd'][2] = os.path.join(self.cfg['work_dir'], i['cmd'][2])
                return_me.append(i['cmd'])
            else:
                print_ignore.append('Ignore {} from {}: {}'.format(i['cmd'][0], i['cmd'][1], i['reason']))
        if len(print_ignore) and self._cli.v:
            print()
            print('\n'.join(print_ignore))
        if len(print_allow):
            print()
            print('\n'.join(print_allow))
        return return_me

    def _triggers_check(self, files: list, change_files: list or None):
        if change_files is None:
            return True, 'clone'
        for file in files:
            if file.startswith('*'):  # Триггер или любой файл
                if len(file) == 1 and len(change_files):  # Любой файл. Триггеры в другом месте проверим
                    return True, 'any'
            elif file.endswith('*'):
                if self._startswith_list(file[:-1], change_files):
                    return True, file
            elif file in change_files:
                return True, file
        return False, None

    @staticmethod
    def _startswith_list(start: str, files: list) -> bool:
        for file in files:
            if file.startswith(start):
                return True
        return False

    @staticmethod
    def _git_triggers_check(files: list, triggers: dict):
        for file in files:
            if file.startswith('*') and len(file) > 1:  # Триггер
                if triggers.get(file[1:], False):
                    return True, file[1:]
        return False, None

    def _generate_git_triggers(self):
        # Обработка GIT_TRIGGERS
        workers = []
        for dir_ in self.git_triggers:
            git = self.git_triggers[dir_]['git']
            file_triggers = self.git_triggers[dir_]['triggers']
            th = threading.Thread(target=self.__git_triggers_th, args=(dir_, git, file_triggers))
            th.start()
            workers.append(th)
        while [True for th in workers if th.is_alive()]:
            time.sleep(0.02)

    def __git_triggers_th(self, dir_, git, file_triggers):
        if not len(file_triggers):
            return
        if git in self.known_repos:
            change_files = self.known_repos[git]['files']
        else:
            git_path = os.path.join(self.cfg['triggers'], dir_)
            full_git_path = os.path.join(self.cfg['work_dir'], git_path)
            change_files = None
            if _is_git(full_git_path):  # Делаем пул
                change_files = _git_pull(full_git_path)
            else:  # клонируем
                if not _git_clone(git, full_git_path):
                    print('Ignore git-triggers from {}'.format(git))
                    return
            self.known_repos[git] = {'dir': git_path, 'files': change_files}
        # Обходим триггеры
        for trigger, file_list in file_triggers.items():
            result, _ = self._triggers_check(file_list, change_files)
            self.filled_triggers[trigger] = self.filled_triggers.get(trigger, False) | result

    def _generate_targets_repo(self):
        workers = [threading.Thread(target=self.__targets_repo_th, args=(t, )) for t in self.targets_all]
        _ = [t.start() for t in workers]
        while [True for th in workers if th.is_alive()]:
            time.sleep(0.02)

    def __targets_repo_th(self, targets):
        if 'git' not in targets or 'dir' not in targets or 'targets' not in targets:
            print('Wrong target, ignore: {}'.format(targets))
            return
        if targets['git'] in self.known_repos:
            targets['dir'] = self.known_repos[targets['git']]['dir']
        else:
            full_git_path = os.path.join(self.cfg['work_dir'], targets['dir'])
            change_files = None
            if _is_git(full_git_path):  # Делаем пул
                change_files = _git_pull(full_git_path)
            else:  # клонируем
                if not _git_clone(targets['git'], full_git_path):
                    print('Ignore {}'.format(targets['git']))
                    return
            self.known_repos[targets['git']] = {'dir': targets['dir'], 'files': change_files}

    def _docker_login(self):
        _docker_login(self.cfg)

    def _prepare_all(self):
        auth_th = threading.Thread(target=self._docker_login, name='_docker_login')
        auth_th.start()
        self._generate_targets_repo()
        self._generate_git_triggers()
        return auth_th.join()

    def _generate(self):
        self._prepare_all()
        if len(self.filled_triggers) and self._cli.v:
            print('git-triggers: {}'.format(self.filled_triggers))
        for targets in self.targets_all:
            git = targets['git']
            if git not in self.known_repos:
                continue
            full_git_path = os.path.join(self.cfg['work_dir'], self.known_repos[git]['dir'])
            change_files = self.known_repos[git]['files']
            tags = _git_get_tags(full_git_path, self.cfg)
            for target in targets['targets']:
                self._target_check(target, change_files, tags, targets['dir'])

    def _target_check(self, target, change_files, tags, git_path):
        is_file_change, change_of = self._triggers_check(target.get('triggers', []), change_files)
        is_triggered, triggered_of = self._git_triggers_check(target.get('triggers', []), self.filled_triggers)
        is_change = self.cfg['force'] or is_file_change or is_triggered
        main_name = '{}/{}'.format(self.cfg['user'], target['registry']) if self.cfg['user'] else target['registry']
        for build in target['build']:
            dockerfile_change = change_files is not None and build[0] in change_files
            is_change |= dockerfile_change
            tag = build[1].format(**tags)
            path = os.path.join(git_path, build[0])
            full_path = os.path.join(self.cfg['work_dir'], path)
            build_name = '{}:{}'.format(main_name, tag)
            e = {
                'reason': '',
                'cmd': [build_name, path, git_path],
                'true': True
            }
            # Пошли проверочки на исключение
            if not len(tag):
                e['reason'] = 'Empty tag'
            elif not len(target['registry']):
                e['reason'] = 'Empty registry'
            elif not is_change:
                e['reason'] = 'No change'
            elif not os.path.isfile(full_path):
                e['reason'] = 'File not found: {}'.format(e['cmd'][1])
            elif self.cfg['arch_detect'] and self.cfg['arch'] != _get_arch_from_dockerfile(full_path):
                e['reason'] = 'Arch no {}'.format(self.cfg['arch'])
            elif build_name in self.all_build_name:
                e['reason'] = 'Name:tag \'{}\' already present'.format(build_name)

            if e['reason']:
                e['true'] = False

            if e['true']:
                # Добавляем причины включения
                if self.cfg['force']:
                    e['reason'] = 'force: True'
                elif dockerfile_change:
                    e['reason'] = 'Dockerfile change'
                elif is_file_change:
                    e['reason'] = 'File change: {}'.format(change_of)
                elif is_triggered:
                    e['reason'] = 'Triggered from git-trigger: {}'.format(triggered_of)
                # Имя билда должно быть уникально
                self.all_build_name.add(build_name)
            self.to_build.append(e)


def docker_prune(targets: list) -> int:
    # Удалить контейнеры и образы из списка реп:тег.
    # Ошибок быть не должно, вообще.
    # Чекает первый элемент, т.е. тоже что вернул generate_builds
    work_time = time.time()
    containers = _docker_containers()
    images_ = _docker_images()

    # Заменяем ид образа у контейнеров на реп:тег. реп:тег всех образов в один список
    images = []
    for image in images_:
        for container in containers:
            if image[0] == container[0]:
                container[0] = image[1]
        images.append(image[1])
    del images_

    for target in targets:
        if target[0] in images:  # А может ли быть контейнер без образа? Да пофиг.
            # сносим контейнеры
            for container in containers:
                if target[0] == container[0]:
                    _docker_prune_container(container[1])
            # И образ
            docker_prune_image(target[0])
    return int(time.time() - work_time)


class SystemD:
    def __init__(self, action):
        self._root_test()
        name = 'docker builder auto'
        f_name = '_'.join(name.lower().split())
        self._files = ['{}.service'.format(f_name), '{}.timer'.format(f_name)]
        self._systemd_path = '/etc/systemd/system/'
        self._path = {
            '_TIME_': '72h',
            '_PARAMS_': self._get_params_str(),
            '_MAIN_': os.path.abspath(sys.argv[0]),
            '_NAME_': name
        }
        self._data = {k: self._getter(k) for k in self._files}
        if action is None:
            raise RuntimeError('Action is None')
        elif action:
            self.install()
        else:
            self.uninstall()

    def install(self):
        for k in self._data:
            path = os.path.join(self._systemd_path, k)
            with open(path, 'w') as fp:
                fp.write(self._data[k])
        self._systemd_reload()
        self._systemd_enable()

    def uninstall(self):
        self._systemd_disable()
        for k in self._data:
            try:
                os.remove(os.path.join(self._systemd_path, k))
            except FileNotFoundError:
                pass
        self._systemd_reload()

    @staticmethod
    def _get_params_str() -> str:
        params = sys.argv[1:]
        for rm in ['--install', '--uninstall']:
            if rm in params:
                params.remove(rm)
        if '-p' not in params:
            params.extend(['-p', os.path.expanduser('~')])
        return ' '.join(params)

    @staticmethod
    def _root_test():
        if os.geteuid() != 0:
            print('--(un)install need root privileges. Use sudo, bye.')
            exit(1)

    @staticmethod
    def _systemd_reload():
        subprocess.run(['systemctl', 'daemon-reload'])

    def _systemd_enable(self):
        subprocess.run(['systemctl', 'enable', self._files[1]])
        subprocess.run(['systemctl', 'start', self._files[1]])

    def _systemd_disable(self):
        subprocess.run(['systemctl', 'stop', self._files[1]])
        subprocess.run(['systemctl', 'disable', self._files[1]])

    def _getter(self, file: str) -> str:
        d = {
            self._files[0]: [
                '[Unit]',
                'Description={_NAME_} job',
                '',
                '[Service]',
                'Type=oneshot',
                'ExecStart=/usr/bin/python3 -u {_MAIN_} {_PARAMS_}'
            ],
            self._files[1]: [
                '[Unit]',
                'Description={_NAME_}',
                '',
                '[Timer]',
                'OnBootSec=15min',
                'OnUnitActiveSec={_TIME_}',
                '',
                '[Install]',
                'WantedBy=timers.target'
            ]
        }
        return '\n'.join(d[file]).format(**self._path)
