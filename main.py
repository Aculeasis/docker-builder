#!/usr/bin/env python3

import time

import docker_builder

CFG = {
    # Каталог где есть\будут лежать git-репы.
    'work_dir': '/root',
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
    'remove_fast': False,
    # Потоков сборки.
    'max_build_t': 2,
    # Потоков пуша
    'max_push_t': 2,
    # Принудительно пересобрать образы.
    'force': True,
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
             'registry': 'mdmt2', 'triggers': ['crutch.py', 'entrypoint.sh'],
             # build: список из списков [файл докера, тег].
             # В теге возможны подстановки, см. DEF_TAGS.
             'build': [
                 ['Dockerfile.amd64',   '{arch}'],
                 ['Dockerfile.arm64v8', '{arch}'],
                 ['Dockerfile.arm32v7', '{arch}'],
             ]
          },
         {
             'registry': 'mdmt2_rhvoice', 'triggers': ['crutch.py', 'entrypoint.sh'],
             'build': [
                 ['Dockerfile_rhvoice.amd64',   '{arch}'],
                 ['Dockerfile_rhvoice.arm64v8', '{arch}'],
                 ['Dockerfile_rhvoice.arm32v7', '{arch}']
             ]
         },
     ]
     },
    {'git': 'https://github.com/Aculeasis/rhvoice-rest',
     'dir': 'rhvoice-rest',
     'targets': [
         {
             'registry': 'rhvoice-rest', 'triggers': ['app.py', ],
             'build': [
                 ['Dockerfile.amd64',   '{arch}'],
                 ['Dockerfile.arm64v8', '{arch}'],
                 ['Dockerfile.arm32v7', '{arch}']
             ]
         },
     ]
     }
]


class Builder:
    def __init__(self, cfg, targets):
        self.cfg = cfg
        self.to_build = docker_builder.generate_builds(self.cfg, targets)
        if len(self.to_build):
            print('\nDocker prune in {} sec\n'.format(docker_builder.docker_prune(self.to_build)))
        else:
            print()
            print('Nothing to do, bye')
        self.building = []
        self.builded = []

        self.pushing = []
        self.pushed = []

        self.build()

    def build(self):
        while len(self.to_build) or len(self.building):
            self.build_check()
            self.push_check()
            self.add_new_build()
            self.add_new_push()
            if self.cfg['remove_fast']:
                self.remove_check()
            time.sleep(5)
        self.push()

    def push(self):
        while self.cfg['auto_push'] and (len(self.builded) or len(self.pushing)):
            self.push_check()
            self.add_new_push()
            if self.cfg['remove_fast']:
                self.remove_check()
            time.sleep(5)
        self.remove_check()
        docker_builder.docker_logout()

    def remove_check(self):
        while self.cfg['remove_after_push'] and self.cfg['auto_push'] and len(self.pushed):
            target = self.pushed.pop(0)
            print('Remove {}'.format(target))
            docker_builder.docker_prune_image(target, False)

    def add_new_build(self):
        while len(self.building) < self.cfg['max_build_t'] and len(self.to_build):
            cmd = self.to_build.pop(0)
            print('Start building {}'.format(cmd[0]))
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


if __name__ == '__main__':
    Builder(CFG, TARGETS)
