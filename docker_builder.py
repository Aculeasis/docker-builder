#!/usr/bin/env python3

import os
import shutil
import socket
import subprocess
import threading
import time

import docker  # pip3 install docker


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
        except TypeError as e:
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
        for retry in range(1, 3):
            try:
                self.err = client.images.push(repository=self._repository, tag=self._tag)
            except socket.timeout:
                print('Error push {}:{}. Retry {}'.format(self._repository, self._tag, retry))
            else:
                _status = 0
                break
        if _status is None:
            self.err += ' socket error'
            _status = 1
        self.work_time = int(time.time() - work_time)
        self._status = _status


def _get_run_stdout(cmd: list, fatal: bool) -> str:
    run = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if run.returncode:
        print('Get error {} from \'{}\': {}'.format(run.returncode, ' '.join(run.args), run.stderr))
        print('Output logs:')
        for l in run.stdout.split('\n')[-5:]:
            print(l)
        if fatal:
            raise RuntimeError('FATAL ERROR!')
    return run.stdout.decode().strip('\n')


def _get_arch() -> str:
    """
    Определяет архитектуру системы.
    :return: архитектура системы или 'unknown'
    """
    aarch = {'x86_64': 'amd64', 'aarch64': 'arm64v8', 'armv7l': 'arm32v7'}
    arch = _get_run_stdout(['uname', '-m'], True)
    if arch not in aarch:  # Тогда вот так
        arch = _get_run_stdout(['uname', '-i'], True)
    if arch not in aarch:
        return 'unknown'
    return aarch[arch]


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
        return run.stdout.decode().strip('\n').split('\0')
    return []


def _git_get_full_hash(path) -> str:
    run = subprocess.run(['git', '-C', path, 'log', '-n', '1'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if not run.returncode:
        # commit 2a2f3f60c7bc168c3121c07fefc84bedf9ed4abd\n -> 2a2f3f60c7bc168c3121c07fefc84bedf9ed4abd or ''
        return run.stdout.decode().split('\n')[0].split(' ')[-1]
    return ''


def _git_get_tags(path: str, cfg: dict) -> dict:
    # вернет заполненные теги
    def get_run(cmd_: list) -> subprocess.run:
        cmd = ['git', '-C', path]
        return subprocess.run(cmd + cmd_, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    tags = DEF_TAGS.copy()
    tags['arch'] = cfg['arch']
    tags['c_full'] = _git_get_full_hash(path)
    if tags['c_full']:
        run = get_run(['rev-parse', '--short=7', tags['c_full']])
        if not run.returncode:
            # 2a2f3f60c7bc168c3121c07fefc84bedf9ed4abd -> 2a2f3f6
            tags['c_short'] = run.stdout.decode().strip('\n')
    run = get_run(['describe', ])
    if not run.returncode:
        # 0.7.1-1-gdc36179
        tags['tag_full'] = run.stdout.decode().strip('\n')
    if tags['tag_full']:
        run = get_run(['describe', '--abbrev=0'])
        if not run.returncode:
            # 0.7.1
            tags['tag'] = run.stdout.decode().strip('\n')
    return tags


def __docker_run_fatal(cmd2: list, fatal: bool = True):
    run = subprocess.run(['docker', ] + cmd2, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
    if run.returncode:
        if fatal:
            raise RuntimeError('Error docker {}: {}'.format(' '.join(cmd2), run.stderr.decode()))
        else:
            print('Error docker {}: {}'.format(' '.join(cmd2), run.stderr.decode()))
    return run


def _docker_login(path, cfg):
    # Читаем логин пасс из файла и логинимся.
    with open(path, encoding='utf8') as f:
        data = [l.strip() for l in f.readline().strip('\n').split(' ', 1)]
    if len(data) != 2:
        raise RuntimeError('Bad credentials, file {}, len={}!=2'.format(path, len(data)))
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


def _cfg_prepare(cfg: dict):
    if not os.path.isdir(cfg['work_dir']):
        raise RuntimeError('\'work_dir\' not found: {}', cfg['work_dir'])
    cfg['triggers'] = os.path.join(cfg['work_dir'], cfg.get('triggers', '.triggers'))
    if not os.path.isdir(cfg['triggers']):
        os.mkdir(cfg['triggers'])
    if cfg['auto_push']:
        path = cfg['credentials'] if cfg['credentials'].startswith('/') \
            else os.path.join(cfg['work_dir'], cfg['credentials'])
        _docker_login(path, cfg)
    elif cfg['remove_after_push']:
        cfg['remove_after_push'] = False
        print('\'auto_push\' is False. Set \'remove_after_push\' to False.')
    cfg['arch'] = _get_arch()
    if cfg['arch_detect'] and cfg['arch'] == 'unknown':
        raise RuntimeError('Unknown architecture and \'arch_detect\' is True')
    print('Architecture: {}'.format(cfg['arch']))
    return cfg


class GenerateBuilds:
    def __init__(self, cfg, targets_all, git_triggers):
        self.cfg = _cfg_prepare(cfg)
        self.targets_all = targets_all
        self.git_triggers = git_triggers
        self.filled_triggers = {}
        self.to_build = []
        self.all_build_name = set()

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
        if len(print_ignore):
            print()
            print('\n'.join(print_ignore))
        if len(print_allow):
            print()
            print('\n'.join(print_allow))
        return return_me

    @staticmethod
    def _triggers_check(files: list, change_files: list or None):
        if change_files is None:
            return True, 'clone'
        for file in files:
            if file.startswith('*'):  # Триггер или любой файл
                if len(file) == 1 and len(change_files):  # Любой файл. Триггеры в другом месте проверим
                    return True, 'any'
            elif file in change_files:
                return True, file
        return False, None

    @staticmethod
    def _git_triggers_check(files: list, triggers: dict):
        for file in files:
            if file.startswith('*') and len(file) > 1:  # Триггер
                if triggers.get(file[1:], False):
                    return True, file[1:]
        return False, None

    def _generate_git_triggers(self):
        # Обработка GIT_TRIGGERS
        for dir_ in self.git_triggers:
            git = self.git_triggers[dir_]['git']
            file_triggers = self.git_triggers[dir_]['triggers']
            if not len(file_triggers):
                continue
            git_path = os.path.join(self.cfg['triggers'], dir_)
            change_files = None
            if _is_git(git_path):  # Делаем пул
                change_files = _git_pull(git_path)
            else:  # клонируем
                if not _git_clone(git, git_path):
                    print('Ignore git-triggers from {}'.format(git))
                    continue
            # Обходим триггеры
            for trigger, file_list in file_triggers.items():
                result, _ = self._triggers_check(file_list, change_files)
                self.filled_triggers[trigger] = self.filled_triggers.get(trigger, False) | result

    def _generate(self):
        self._generate_git_triggers()
        if len(self.filled_triggers):
            print('git-triggers: {}'.format(self.filled_triggers))
        for targets in self.targets_all:
            if 'git' not in targets or 'dir' not in targets or 'targets' not in targets:
                print('Wrong target, ignore: {}'.format(targets))
                continue
            full_git_path = os.path.join(self.cfg['work_dir'], targets['dir'])
            change_files = None
            if _is_git(full_git_path):  # Делаем пул
                change_files = _git_pull(full_git_path)
            else:  # клонируем
                if not _git_clone(targets['git'], full_git_path):
                    print('Ignore {}'.format(targets['git']))
                    continue
            tags = _git_get_tags(full_git_path, self.cfg)
            for target in targets['targets']:
                self._target_check(target, change_files, tags, targets['dir'])

    def _target_check(self, target, change_files, tags, git_path):
        is_file_change, change_of = self._triggers_check(target.get('triggers', []), change_files)
        is_triggered, triggered_of = self._git_triggers_check(target.get('triggers', []), self.filled_triggers)
        is_change = self.cfg['force'] or is_file_change or is_triggered
        main_name = '{}/{}'.format(self.cfg['user'], target['registry']) if self.cfg['user'] else target['registry']
        for build in target['build']:
            is_change |= build[0] in change_files
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
            elif build_name in self.all_build_name:
                e['reason'] = 'Name:tag \'{}\' already present'.format(build_name)
            elif self.cfg['arch_detect'] and self.cfg['arch'] != _get_arch_from_dockerfile(full_path):
                e['reason'] = 'Wrong architecture: not {}'.format(self.cfg['arch'])

            if e['reason']:
                e['true'] = False

            if e['true']:
                # Добавляем причины включения
                if self.cfg['force']:
                    e['reason'] = 'force: True'
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
