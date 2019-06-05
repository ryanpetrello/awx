from setuptools import setup
from setuptools.command.sdist import sdist


class SDist(sdist):
    def run(self):
        import sys
        sys.path.insert(1, '/awx_devel')

        import django
        from awx import prepare_env

        import json
        import os

        prepare_env()
        django.setup()

        import awx
        from awx.api.swagger import generate
        with open(os.path.join(
            os.path.abspath(os.path.dirname(__file__)),
            'awxclient',
            'schema.py'
        ), 'w') as f:
            f.write('# -*- coding: utf-8 -*-\n')
            f.write('from collections import OrderedDict;\nschema=')
            f.write(str(generate()))
        sdist.run(self)


setup(
    name = "awxclient",
    version = '0.0.1',
    author = "Ansible, Inc.",
    author_email = "info@ansible.com",
    description='An SDK/CLI library for Ansible AWX',
    license='Apache License 2.0',
    keywords='ansible',
    url='http://github.com/ansible/awx',
    packages=['awxclient'],
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Environment :: Web Environment',
        'License :: Apache License 2.0',
        'Natural Language :: English',
        'Operating System :: OS Independent',
        'Operating System :: POSIX',
        'Programming Language :: Python',
    ],
    install_requires=['bravado'],
    cmdclass={
        'sdist': SDist,
    },
)
