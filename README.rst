.. contents::

GUILLOTINA_S3STORAGE
====================

S3 blob storage for guillotina.

The bucket name is built from the container id and the 'bucket' setting::

    "<container>.<bucket>"

Example config.json:

.. code-block:: json

    "applications": [
        "guillotina_s3storage"
    ]

    "load_utilities": {
        "s3": {
            "provides": "guillotina_s3storage.interfaces.IS3BlobStore",
            "factory": "guillotina_s3storage.storage.S3BlobStore",
            "settings": {
                "aws_client_id": "<client id>",
                "aws_client_secret": "<client secret>",
                "bucket": "<bucket name suffix>",
                "bucket_name_format": "{container}{delimiter}{base}",
                "endpoint_url": null,
                "ssl": true,
                "verify_ssl": null,
                "region_name": null,
                "max_pool_connections": 30,
                "connect_timeout": 5,
                "read_timeout": 10,
                "max_attempts": 2
            }
        }
    }

``connect_timeout`` and ``read_timeout`` are in seconds. ``read_timeout`` also
bounds each socket read of a download's body stream. ``max_attempts`` is the
total number of botocore attempts per request, including the first.


Getting started with development
--------------------------------

Using pip (requires Python > 3.7)

.. code-block:: shell

    python3.7 -m venv .
    ./bin/pip install -e .[test]
    pre-commit install
