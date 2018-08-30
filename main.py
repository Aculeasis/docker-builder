#!/usr/bin/env python3

import time

import docker_builder

CFG = {
    # каталог где есть\будут лежать все репы
    'work_dir': '/root',
    # Его перезапишет при логине.
    'user': '',
    # файл с login pass от хаба в work_dir. Если начинается с / то абсолютный путь.
    'credentials': '.docker_credentials',
    # Автоматически определять архитектуру докерфайлов по FROM и игнорировать неподдерживаемые
    'arch_detect': True,
    # Пушить образы в хаб
    'auto_push': True,
    # Удалить запушенные образы.
    'remove_after_push': True,
    # !!!: Файлы будут удалены сразу а не в самом конце. Сломает зависимости сборки, если они есть
    'remove_fast': False,
    # Потоков сборки
    'max_build_t': 2,
    # Потоков пуша
    'max_push_t': 2,
    # Принудительно пересобрать образы
    'force': False,
}

TARGETS = [
    {'git': 'https://github.com/Aculeasis/mdmt2-docker',
     'dir': 'mdmt2-docker',
     'targets': [
         {
             'registry': 'mdmt2', 'triggers': ['crutch.py', 'entrypoint.sh'],
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
            w_time = time.time()
            docker_builder.docker_prune(self.to_build)
            print('\nDocker prune in {} sec\n'.format(int(time.time() - w_time)))
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
            time.sleep(20)
        self.push()

    def push(self):
        while self.cfg['auto_push'] and (len(self.builded) or len(self.pushing)):
            self.push_check()
            self.add_new_push()
            if self.cfg['remove_fast']:
                self.remove_check()
            time.sleep(20)
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
            self.building.append([docker_builder.Build(*cmd), cmd[0], time.time()])

    def add_new_push(self):
        while self.cfg['auto_push'] and len(self.pushing) < self.cfg['max_push_t'] and len(self.builded):
            cmd = self.builded.pop(0)
            print('Start pushing {}'.format(cmd))
            self.pushing.append([docker_builder.Push(cmd), cmd, time.time()])

    @staticmethod
    def _x_check(src: list, dst: list, name: str):
        if not len(src):
            return
        check = True
        while check:
            check = False
            for el in range(0, len(src)):
                if src[el][0].status() is not None:  # Завершился
                    i = src.pop(el)
                    if not i[0].status():
                        print('{} {} successful in {} sec'.format(name, i[1], int(time.time() - i[2])))
                        dst.append(i[1])
                    else:
                        print('{} {} failed [{}]: {}'.format(name, i[1], i[0].status(), i[0].err))
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
