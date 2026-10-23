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
                "connect_timeout": null,
                "read_timeout": null,
                "max_attempts": null,
                "download_timeout": 30,
                "download_max_resumes": 3
            }
        }
    }

``connect_timeout``, ``read_timeout`` and ``max_attempts`` configure the S3
client for every call. Unset, they use the botocore defaults. ``read_timeout`` is
in seconds and also bounds each socket read of a download's body stream.
``max_attempts`` is the total number of botocore attempts per request, including
the first.

``download_timeout`` bounds, in seconds, the wait for a pooled connection plus
the time to the response headers of each download request. A download that
fails before any response arrives raises at once. A download whose body read
stalls or breaks resumes from the last byte received, up to
``download_max_resumes`` times in a row without progress, and fails if the
object changed in between.


Getting started with development
--------------------------------

Using pip (requires Python > 3.7)

.. code-block:: shell

    python3.7 -m venv .
    ./bin/pip install -e .[test]
    pre-commit install
