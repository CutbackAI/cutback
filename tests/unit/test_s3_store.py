import io
import os
from uuid import uuid4

from cutback.evals.artifacts import EncryptedS3Store


def test_s3_driver_encrypts_before_transport_and_matches_storage_contract():
    class S3:
        def __init__(self):
            self.objects = {}

        def put_object(self, *, Bucket, Key, Body, ContentType):
            self.objects[(Bucket, Key)] = Body

        def get_object(self, *, Bucket, Key):
            return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

        def delete_object(self, *, Bucket, Key):
            self.objects.pop((Bucket, Key), None)

    client = S3()
    store = EncryptedS3Store("synthetic-customer-bucket", os.urandom(32), client=client)
    project = uuid4()
    ref = store.put(project, b"PRIVATE payload", 1)
    assert b"PRIVATE" not in next(iter(client.objects.values()))
    assert store.get(project, ref) == b"PRIVATE payload"
    store.delete(project, ref)
    assert client.objects == {}
