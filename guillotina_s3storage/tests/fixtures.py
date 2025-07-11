import os

from guillotina import testing


def settings_configurator(settings):
    if "applications" in settings:
        settings["applications"].append("guillotina_s3storage")
    else:
        settings["applications"] = ["guillotina_s3storage"]

    settings["load_utilities"]["s3"] = {
        "provides": "guillotina_s3storage.interfaces.IS3BlobStore",
        "factory": "guillotina_s3storage.storage.S3BlobStore",
        "settings": {
            "bucket": os.environ.get("S3CLOUD_BUCKET", "testbucket"),
            "bucket_name_format": "{container}{delimiter}{base}",
            "aws_client_id": os.environ.get("S3CLOUD_ID", "x" * 10),
            "aws_client_secret": os.environ.get("S3CLOUD_SECRET", "x" * 10),  # noqa
            "region_name": os.environ.get("S3REGION_NAME", "eu-west-3"),
        },
    }

    if "S3CLOUD_ID" not in os.environ:
        settings["load_utilities"]["s3"]["settings"].update(
            {"endpoint_url": "http://localhost:4566", "ssl": False}
        )

    if "S3_HOST" in os.environ:
        settings["load_utilities"]["s3"]["settings"].update(
            {"endpoint_url": f"http://{os.environ['S3_HOST']}:4566", "ssl": False}
        )


testing.configure_with(settings_configurator)
