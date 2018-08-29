#!/usr/bin/env python3

import subprocess
import os
import shutil


# DEF_TARGETS = [
#     # ссылка на гит
#     {'git': 'https://github.com/Aculeasis/mdmt2-docker',
#      # Директория в work_dir. Если это уже гит репа то сделать pull, если нет (удалить если есть) и сделать clone
#      'dir': 'mdmt2-docker',
#      # список целей из репа
#      'targets': [
#          # Лист из диктов. registry: регистр на хабе.
#          # triggers: список из файлов триггеров, их обновление также запустит сборку. Может быть опущен
#          # build: список из список типа [файл докера, таг]
#          # В таге можно юзать {}: {arch} - архитекрута в терминологии докера amd64, arm64v8, arm32v7 или unknown
#          # {c_full} - полный хеш коммита, например dc361791f21a4132dcf4f385b69c4448fa9a48c1
#          # {c_short} - сокращенный хеш, например dc36179
#          # {tag} - таг, например 0.7.1
#          # {tag_full} - полный таг, например 0.7.1-1-gdc36179
#          # При pull соберутся только измененные файлы, при clone - все
#             {
#                 'registry': 'mdmt2', 'triggers': [],
#                 'build': [
#                     ['Dockerfile.amd64', '{arch}'],
#                     ['Dockerfile.arm64v8', '{arch}'],
#                     ['Dockerfile.arm32v7', '{arch}'],
#                 ]
#             },
#             {
#                 'registry': 'mdmt2_rhvoice', 'triggers': [],
#                 'build': [
#                     ['Dockerfile_rhvoice.amd64', '{arch}'],
#                     ['Dockerfile_rhvoice.arm64v8', '{arch}'],
#                     ['Dockerfile_rhvoice.arm32v7', '{arch}']
#                 ]
#             },
#      ]
#      },
#     ]


# TODO: Обработка зависимостей, например 'depends'=[] список зависимостей типа индекс\имя, индекс\имя. Хз как лучше.
# TODO: Переписать работу с докером с subprocess на docker
# FIXME: Очень страшный TARGETS, надо переделать.

DEF_TAGS = {'arch': '', 'c_full': '', 'c_short': '', 'tag': '', 'tag_full': ''}


def _get_run_stdout(cmd: list, fatal: bool) -> str:
    run = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if run.returncode != 0:
        print('Get error {} from \'{}\': {}'.format(run.returncode, ' '.join(run.args), run.stderr))
        print('Output logs:')
        for l in run.stdout.split('\n')[-5:]:
            print(l)
        if fatal:
            raise RuntimeError('FATAL ERROR!')
    return run.stdout.decode().strip('\n')


def _get_arch() -> str:
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
    return run.returncode == 0


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
    if run.returncode == 0:
        # commit 2a2f3f60c7bc168c3121c07fefc84bedf9ed4abd\n -> 2a2f3f60c7bc168c3121c07fefc84bedf9ed4abd or ''
        return run.stdout.decode().split('\n')[0].split(' ')[-1]
    return ''


def _git_get_tags(path, cfg) -> dict:
    # вернет заполненные таги
    def get_run(cmd_: list) -> subprocess.run:
        cmd = ['git', '-C', path]
        return subprocess.run(cmd + cmd_, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    tags = DEF_TAGS.copy()
    tags['arch'] = cfg['arch']
    tags['c_full'] = _git_get_full_hash(path)
    if tags['c_full']:
        run = get_run(['rev-parse', '--short=7', tags['c_full']])
        if run.returncode == 0:
            # 2a2f3f60c7bc168c3121c07fefc84bedf9ed4abd -> 2a2f3f6
            tags['c_short'] = run.stdout.decode().strip('\n')
    run = get_run(['describe', ])
    if run.returncode == 0:
        # 0.7.1-1-gdc36179
        tags['tag_full'] = run.stdout.decode().strip('\n')
    if tags['tag_full']:
        run = get_run(['describe', '--abbrev=0'])
        if run.returncode == 0:
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
    # читаем логин пасс и логинимся. Думаю не стоит их принтить
    with open(path, encoding='utf8') as f:
        data = f.readline().strip('\n'). split(' ', 1)
    if len(data) != 2:
        raise RuntimeError('Bad credentials, file {}, len={}!=2'.format(path, len(data)))
    run = subprocess.run(
        ['docker', 'login', '-u', data[0].strip(), '-p', data[1].strip()],
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE
    )
    cfg['user'] = data[0].strip()
    if run.returncode:
        raise RuntimeError('Error docker login: {}'.format(run.stderr.decode()))
    pass


def docker_logout():
    __docker_run_fatal(['logout', ])


def _docker_containers():
    # вернет список из [ид образа, имя контейнера], ps -a --format "{{.Image}} {{.Names}}"
    run = __docker_run_fatal(['ps', '-a', '--format', '{{.Image}} {{.Names}}'])
    return [line.split(' ', 1) for line in run.stdout.decode().split('\n') if len(line) > 2]


def _docker_images():
    # Вернет список из [ид образа, реп:таг], docker images --format "{{.ID}} {{.Repository}}:{{.Tag}}
    run = __docker_run_fatal(['images', '--format', '{{.ID}} {{.Repository}}:{{.Tag}}'])
    return [line.split(' ', 1) for line in run.stdout.decode().split('\n') if len(line) > 2]


def _docker_prune_container(name: str):
    # Остановит и удалит контейнер
    __docker_run_fatal(['stop', name])
    __docker_run_fatal(['rm', name])


def docker_prune_image(name: str, fatal: bool = True):
    # Удалит образ по реп:таг
    __docker_run_fatal(['rmi', name], fatal)


def _cfg_prepare(cfg: dict):
    if not os. path.isdir(cfg['work_dir']):
        raise RuntimeError('\'work_dir\' not found: {}', cfg['work_dir'])
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


def __list_in_list(list1, list2):
    for l in list1:
        if l in list2:
            return True
    return False


def generate_builds(cfg, targets_all):
    # Все что нужно для сборки. После можно удалить TARGETS
    # Обновляет локальные репы

    # Вернет список из списков, состоящих из полного имени билда (-t), абсолютного пути к докерфайлу (-f)
    # и пути до директории сборки
    # docker build -t foo[0] -f foo[1] foo[2]
    _cfg_prepare(cfg)
    to_build = []
    all_build_name = set()
    for targets in targets_all:
        if 'git' not in targets or 'dir' not in targets or 'targets' not in targets:
            print('Wrong target, ignore: {}'.format(targets))
            continue
        git_path = os.path.join(cfg['work_dir'], targets['dir'])
        change_files = None
        if _is_git(git_path):  # Делаем пул
            change_files = _git_pull(git_path)
        else:  # клонируем
            if not _git_clone(targets['git'], git_path):
                print('Ignore {}'.format(targets['git']))
                continue
        tags = _git_get_tags(git_path, cfg)
        for target in targets['targets']:
            is_change = cfg['force'] or change_files is None or __list_in_list(target.get('triggers', []), change_files)
            main_name = '{}/{}'.format(cfg['user'], target['registry']) if cfg['user'] else target['registry']
            for build in target['build']:
                is_change = is_change or build[0] in change_files
                tag = build[1].format(**tags)
                full_path = os.path.join(git_path, build[0])
                build_name = '{}:{}'.format(main_name, tag)
                e = {
                    'reason': '',
                    'cmd': [build_name, full_path, git_path]
                }
                # Пошли проверочки
                if not len(tag):
                    e['reason'] = 'Empty tag'
                elif not len(target['registry']):
                    e['reason'] = 'Empty registry'
                elif not is_change:
                    e['reason'] = 'No change'
                elif not os.path.isfile(e['cmd'][1]):
                    e['reason'] = 'File not found: {}'.format(e['cmd'][1])
                elif build_name in all_build_name:
                    e['reason'] = 'Name:tag \'{}\' already present'.format(build_name)
                elif cfg['arch_detect'] and cfg['arch'] != _get_arch_from_dockerfile(e['cmd'][1]):
                    e['reason'] = 'Wrong architecture: not {}'.format(cfg['arch'])

                # Имя билда должно быть уникально
                if not e['reason']:
                    all_build_name.add(build_name)
                to_build.append(e)
    # Уф, вышли
    return_me = []
    first = True
    for i in to_build:
        if not i['reason']:
            return_me.append(i['cmd'])
        else:
            if first:
                first = False
                print()
            print('Ignore {} from {}: {}'.format(i['cmd'][0], i['cmd'][1], i['reason']))
    if len(return_me):
        print()
    for i in return_me:
        print('Allow building {} from {}'.format(i[0], i[1]))
    return return_me


def docker_prune(targets: list):
    # Удалить контейнеры и образы из списка реп:таг.
    # Ошибок быть не должно, вообще.
    # Чекает первый элемент, т.е. тоже что вернул generate_builds
    containers = _docker_containers()
    images_ = _docker_images()

    # Заменяем ид образа у контейнеров на реп:таг. реп:таг всех образов в один список
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
