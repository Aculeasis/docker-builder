#!/usr/bin/env python3

import argparse
import json
import os
import time

import docker_builder

CFG = {
    # Каталог где есть\будут лежать git-репы.
    'work_dir': os.path.expanduser('~'),
    # Субдиректория с триггерами
    'triggers': '.triggers',
    # ID в хабе. Если auto_push: True то перезапишет при логине.
    'user': '',
    # Файл с login pass от хаба в work_dir. Если начинается с / то абсолютный путь.
    # Логинимся только с auto_push: True.
    'credentials': '.docker_credentials',
    # Автоматически определять архитектуру докерфайлов по FROM и игнорировать неподдерживаемые.
    'arch_detect': True,
    # Пушить образы в хаб.
    'auto_push': True,
    # Удалить запушенные образы.
    'remove_after_push': True,
    # !!!: Файлы будут удалены сразу а не в самом конце. Сломает зависимости сборки, если они есть.
    'remove_fast': True,
    # Потоков сборки.
    'max_build_t': 1,
    # Потоков пуша
    'max_push_t': 1,
    # Принудительно пересобрать образы.
    'force': False,
    # Выполнить docker system prune -f, если что-то собирали
    'prune': False,
}

TARGETS = [
    {'git': 'https://github.com/Aculeasis/mdmt2-docker',  # Ссылка на гит.
     # Директория в work_dir. Если это уже гит репа то сделать pull, если нет (удалить если есть) и сделать clone.
     # При clone будут пересобраны все цели, при pull только измененные.
     'dir': 'mdmt2-docker',
     # Список целей из репа.
     'targets': [
         {   # registry - регистр на хабе.
             # triggers: список из файлов-триггеров, их обновление также активирует сборку. Может быть опущен.
             # Если начинается с * - это триггер из GIT_TRIGGERS
             # Если просто * - изменение любого файла
             # * в конце - любой файл начинающийся с (пути без начального слеша). 'src*' in 'src/main.py' - > True
             'registry': 'mdmt2', 'triggers': ['crutch.py', 'entrypoint.sh', '*mdmt2'],
             # Создаст и запушит универсальный тег latest, очень криво и будет ломаться если теги обновляемые (наверное)
             'manifest': ['amd64', 'arm64v8', 'arm32v7'],
             # build: список из списков [файл докера, тег].
             # Добавлять докерфайлы в triggers не нужно.
             # В теге возможны подстановки, см. DEF_TAGS.
             'build': [
                 ['Dockerfile.amd64',   '{arch}'],
                 ['Dockerfile.arm64v8', '{arch}'],
                 ['Dockerfile.arm32v7', '{arch}'],
             ]
          },
     ]
     },
    {'git': 'https://github.com/Aculeasis/rhvoice-rest',
     'dir': 'rhvoice-rest',
     'targets': [
         {
             'registry': 'rhvoice-rest', 'triggers': ['*rhv', '*rhv_dict'],
             'manifest': ['amd64', 'arm64v8', 'arm32v7'],
             'build': [
                 ['Dockerfile.amd64',   '{arch}'],
                 ['Dockerfile.arm64v8', '{arch}'],
                 ['Dockerfile.arm32v7', '{arch}']
             ]
         },
     ]
     },
    {'git': 'https://github.com/Aculeasis/pocketsphinx-rest',
     'dir': 'pocketsphinx-rest',
     'targets': [
         {
             'registry': 'pocketsphinx-rest', 'triggers': ['app.py', 'entrypoint.sh'],
             'build': [
                 ['Dockerfile.amd64',   '{arch}'],
                 ['Dockerfile.arm64v8', '{arch}'],
                 ['Dockerfile.arm32v7', '{arch}']
             ]
         },
     ]
     },
    {'git': 'https://github.com/Aculeasis/vosk-rest',
     'dir': 'vosk-rest',
     'targets': [
         {
             'registry': 'vosk-rest', 'triggers': ['app.py', 'entrypoint.sh'],
             'manifest': ['amd64', 'arm64v8', 'arm32v7'],
             'build': [
                 ['Dockerfile.amd64', '{arch}'],
                 ['Dockerfile.arm64v8', '{arch}'],
                 ['Dockerfile.arm32v7', '{arch}']
             ]
         },
     ]
     },
]

GIT_TRIGGERS = {
    # Делает git clone\pull так же как с TARGETS, но в work_dir/triggers
    # Сами триггеры похожи на targets, но вместо сборки образа они устанавливают триггер в True или False
    # Имя триггера может повторяться, при этом конечный результат будет выражен через логический or.
    # Т.е. если есть a: False и a: True будет False | True = True
    # Главный недостаток в том, что все эти репы будут занимать место -_-
    # Ключ - директория
    'RHVoice-dictionary': {
        'git': 'https://github.com/vantu5z/RHVoice-dictionary',
        'triggers': {
            'rhv_dict': ['*', ],
        }
    },
    'mdmTerminal2': {
        'git': 'https://github.com/Aculeasis/mdmTerminal2',
        'triggers': {
            'mdmt2': ['src/*', ],
        },
    },
    'rhvoice-rest': {
        'git': 'https://github.com/Aculeasis/rhvoice-rest',
        'triggers': {
            'rhv': ['app.py', 'entrypoint.sh', 'rhvoice_proxy/rhvoice*'],
        }
    },
}


class Builder:
    def __init__(self, cfg, targets, git_triggers, args, install):
        self.cfg = cfg
        self.targets = targets
        self.git_triggers = git_triggers
        self.args = args
        self.install = install
        self.count = 0
        self.to_build = []
        self.building = []
        self.builded = []
        self.pushing = []
        self.pushed = []
        self.manifests_pushing = []
        self.manifests = {}

    def start(self):
        if self.install is not None:
            docker_builder.SystemD(self.install)
            return
        self.to_build, self.manifests = docker_builder.GenerateBuilds(
            self.cfg, self.targets, self.git_triggers, self.args
        ).get()
        if len(self.to_build) and not self.args.nope:
            work_time = docker_builder.docker_prune(self.to_build)
            if self.args.v:
                print('\nDocker prune in {} sec\n'.format(work_time))
        elif self.args.v:
            print()
            print('Nothing to do, bye')
        if not self.args.nope:
            self.build()
        docker_builder.docker_logout()

    def build(self):
        while len(self.to_build) or len(self.building):
            self.build_check()
            self.push_check()
            self.add_new_build()
            self.add_new_push()
            if self.cfg['remove_fast']:
                self.pushed_check()
            time.sleep(5)
        self.push()
        self.manifests_check()

    def push(self):
        while self.cfg['auto_push'] and (len(self.builded) or len(self.pushing)):
            self.push_check()
            self.add_new_push()
            if self.cfg['remove_fast']:
                self.pushed_check()
            time.sleep(5)
        self.pushed_check()

    def pushed_check(self):
        while self.cfg['auto_push'] and len(self.pushed):
            target = self.pushed.pop(0)
            if self.cfg['remove_after_push']:
                print('Remove {}'.format(target))
                docker_builder.docker_prune_image(target, False)
            self.add_new_manifest_push(target)

    def manifests_check(self):
        results = []
        while self.manifests_pushing:
            self._x_check(self.manifests_pushing, results, 'Manifest')
            time.sleep(1)

    def add_new_manifest_push(self, target: str):
        target = target.split(':', 1)[0]
        if target in self.manifests:
            self.manifests_pushing.append(docker_builder.ManifestPush(target, self.manifests[target]))

    def add_new_build(self):
        while len(self.building) < self.cfg['max_build_t'] and len(self.to_build):
            cmd = self.to_build.pop(0)
            print('Start building {}'.format(cmd[0]))
            self.count += 1
            self.building.append(docker_builder.Build(*cmd))

    def add_new_push(self):
        while self.cfg['auto_push'] and len(self.pushing) < self.cfg['max_push_t'] and len(self.builded):
            cmd = self.builded.pop(0)
            print('Start pushing {}'.format(cmd))
            self.pushing.append(docker_builder.Push(cmd))

    @staticmethod
    def _x_check(src: list, dst: list, name: str):
        if not len(src):
            return
        check = True
        while check:
            check = False
            for el in range(0, len(src)):
                if src[el].status() is not None:
                    i = src.pop(el)
                    if not i.status():
                        print('{} {} successful in {} sec'.format(name, i.tag, i.work_time))
                        dst.append(i.tag)
                    else:
                        print('{} {} failed [{}]: {}'.format(name, i.tag, i.status(), i.err))
                        print()
                    check = True
                    break

    def push_check(self):
        if self.cfg['auto_push']:
            self._x_check(self.pushing, self.pushed, 'Push')

    def build_check(self):
        self._x_check(self.building, self.builded, 'Build')


def json_loader(fp: open, type_: type):
    data = None
    try:
        data = json.load(fp)
        if type(data) is not type_:
            raise ValueError('Wrong object: \'{}\' is not \'{}\''.format(type(data).__name__, type_.__name__))
    except (json.JSONDecodeError, ValueError, IOError) as e:
        print('Error load {}: {}'.format(fp.name, str(e)))
        fp.close()
        exit(1)
    else:
        fp.close()
    return data


def cl_parse():
    parser = argparse.ArgumentParser()
    parser.add_argument('-n', '--nope', action='store_true', help='Don\'t build images')
    parser.add_argument('-f', '--force', action='store_true', help='Set force to True')
    parser.add_argument('--prune', action='store_true', help='Run system prune -f after building')
    parser.add_argument('-v', action='store_true', help='Increase verbosity level')
    parser.add_argument('-c', metavar='FILE', type=open, help='File with settings, in json')
    parser.add_argument('-t', metavar='FILE', type=open, help='File with build targets, in json')
    parser.add_argument('-g', metavar='FILE', type=open, help='File with git-triggers, in json')
    parser.add_argument('-p', metavar='PATH', help='rewrite work_dir')
    one = parser.add_mutually_exclusive_group()
    one.add_argument('--install', action='store_true', help='Install systemd unit')
    one.add_argument('--uninstall', action='store_true', help='Remove systemd unit')
    args = parser.parse_args()
    cfg = json_loader(args.c, dict) if args.c else CFG
    targets = json_loader(args.t, list) if args.t else TARGETS
    git_triggers = json_loader(args.g, dict) if args.g else GIT_TRIGGERS
    if args.force:
        cfg['force'] = True
    if args.prune:
        cfg['prune'] = True
    cfg['work_dir'] = args.p or cfg['work_dir']
    install = None
    if args.install:
        install = True
    elif args.uninstall:
        install = False
    return cfg, targets, git_triggers, args, install


def main():
    cl_data = cl_parse()
    prune = cl_data[0]['prune']
    builder = Builder(*cl_data)
    try:
        builder.start()
    except Exception as _e:
        print('Internal error: {}'.format(_e))

    if prune and builder.count:
        print('Run system prune...')
        docker_builder.docker_system_prune()


if __name__ == '__main__':
    main()
