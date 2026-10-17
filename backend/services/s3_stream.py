"""S3 (SeaweedFS) streaming access for run artifacts.

The gateway is reached through the host TCP forwarder (see
tools/executor/forwarder.py) because rootless containers cannot use the
host loopback; endpoint and credentials come from environment variables.
"""
from __future__ import annotations

import os

import boto3
from botocore.config import Config


def s3_client():
    endpoint = os.environ.get("S3_ARTIFACT_ENDPOINT_URL", "http://127.0.0.1:8333")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ.get("MINIO_ACCESS_KEY", ""),
        aws_secret_access_key=os.environ.get("MINIO_SECRET_KEY", ""),
        region_name="us-east-1",
        config=Config(connect_timeout=5, read_timeout=60, retries={"max_attempts": 1}),
    )


def artifact_bucket() -> str:
    return (
        os.environ.get("S3_ARTIFACT_BUCKET")
        or os.environ.get("MINIO_BUCKET", "open-foundation")
    )
