#!/usr/bin/env python
from distutils.core import setup

setup(
    name='composer',
    version='1.0',
    description='Charm Composition Tooling',
    author='Juju Solutions Team',
    author_email='benjamin.saller@canonical.com',
    url='https://github.com/bcsaller/juju_compose',
    packages=['juju_compose'],
    entry_points={
        'console_scripts': [
            'juju-compose = juju_compose:main',
            'juju-inspect = juju_compose:inspect',
        ]
    }
)
