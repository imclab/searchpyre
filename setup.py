#!/usr/bin/python

from setuptools import setup, find_packages


setup(
    name = 'searchpyre',
    version =  __import__('searchpyre').__version__,
    description = 'Simple Python Search Engine built on Redis',
    long_description = open('README.rst').read(),
    author = 'Eugene Baumstein',
    author_email = 'eugene.baumstein@gmail.com',
    url = 'https://github.com/ebaum/searchpyre',
    py_modules = ['searchpyre'],
    install_requires = ['redis'],
    license = 'MIT',
    packages = find_packages(),
    classifiers = [
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python',
        'Topic :: Internet',
        'Topic :: Software Development :: Libraries :: Python Modules',
    ],
)
