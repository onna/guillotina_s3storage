# -*- coding: utf-8 -*-
from setuptools import find_packages
from setuptools import setup


setup(
    name="guillotina_s3storage",
    description="s3 guillotina storage support",
    version=open("VERSION").read().strip(),
    long_description=(open("README.rst").read() + "\n" + open("CHANGELOG.rst").read()),
    classifiers=[
        "Programming Language :: Python :: 3.6",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    author="Ramon Navarro Bosch",
    author_email="ramon@plone.org",
    keywords="async aiohttp guillotina s3",
    url="https://pypi.python.org/pypi/guillotina_s3storage",
    license="GPL version 3",
    setup_requires=[
        "pytest-runner",
    ],
    zip_safe=True,
    include_package_data=True,
    packages=find_packages(exclude=["ez_setup"]),
    package_data={"": ["*.txt", "*.rst"], "guillotina_s3storage": ["py.typed"]},
    install_requires=[
        "setuptools",
        "guillotina @ git+https://github.com/onna/guillotina@master#egg=guillotina",
        "aiohttp==3.10.2",
        "ujson",
        "aiobotocore==2.23.0",
        "botocore==1.38.27",
        "backoff",
        "zope-interface<6,>=5.0.0"
    ],
    extras_require={
        "test": [
            "pytest==8.2.2",
            "pytest-aiohttp==1.0.5",
            "pytest-docker-fixtures",
            "async_asgi_testclient",
            "prometheus_client",
        ]
    },
)
