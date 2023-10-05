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
        "guillotina>=5.0.0,<6",
        "aiohttp>=3.3.1,<4.0.0",
        "ujson",
        "aiobotocore==2.3.3",
        "botocore==1.24.21",
        "backoff",
        "zope.interface>=5.0.0,<6"
    ],
    extras_require={
        "test": [
            "pytest>=6.0.0,<7",
            "pytest-aiohttp>=0.3.0,<1",
            "pytest-docker-fixtures",
            "async_asgi_testclient",
            "prometheus_client",
        ]
    },
)
