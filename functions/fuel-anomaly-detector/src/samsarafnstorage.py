import base64
import json
import os
import boto3
import botocore.exceptions


class Storage:
    def __init__(self, credentials: dict[str, str]):
        self.client = boto3.client("s3", **credentials)
        self.bucket = os.environ["SamsaraFunctionStorageName"]

    def put(self, Key: str, Body: bytes, **kwargs):
        return self.client.put_object(Bucket=self.bucket, Key=Key, Body=Body, **kwargs)

    def put_base64(self, Key: str, Base64: str, **kwargs):
        return self.put(Key, Body=base64.b64decode(Base64), **kwargs)

    def get(self, Key: str, **kwargs):
        return self.client.get_object(Bucket=self.bucket, Key=Key, **kwargs)

    def get_body(self, Key: str, **kwargs) -> bytes:
        return self.get(Key, **kwargs)["Body"].read()

    def get_body_base64(self, Key: str, **kwargs) -> str:
        return base64.b64encode(self.get_body(Key, **kwargs)).decode("utf-8")

    def delete(self, Key: str, **kwargs):
        return self.client.delete_object(Bucket=self.bucket, Key=Key, **kwargs)

    def list_objects(self, Prefix: str = "", **kwargs):
        return self.client.list_objects_v2(Bucket=self.bucket, Prefix=Prefix, **kwargs)

    def list_contents(self, Prefix: str = "", **kwargs):
        return self.list_objects(Prefix=Prefix, **kwargs).get("Contents", [])

    def list_keys(self, Prefix: str = "", **kwargs) -> list[str]:
        return [
            obj.get("Key")
            for obj in self.list_contents(Prefix=Prefix, **kwargs)
            if obj.get("Key")
        ]

    def generate_presigned_url(self, Key: str, expiry_seconds: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": Key},
            ExpiresIn=expiry_seconds,
        )


class Database:
    def __init__(self, storage: Storage, namespace: str):
        self.storage = storage
        self.namespace = namespace.strip(" /")

    def __key(self, key: str) -> str:
        return f"{self.namespace}/{key}"

    def keys(self) -> list[str]:
        return [
            k.removeprefix(self.namespace + "/")
            for k in self.storage.list_keys(Prefix=self.namespace)
        ]

    def get(self, key: str) -> str | None:
        try:
            return self.storage.get_body(Key=self.__key(key)).decode("utf-8")
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                return None
            raise e

    def get_dict(self, key: str) -> dict | None:
        value = self.get(key)
        return json.loads(value) if value is not None else None

    def put(self, key: str, value: str):
        return self.storage.put(Key=self.__key(key), Body=value.encode("utf-8"))

    def put_dict(self, key: str, value: dict):
        return self.put(key, json.dumps(value))

    def delete(self, key: str):
        return self.storage.delete(Key=self.__key(key))


_credentials: None | dict[str, str] = None


def get_credentials(force_refresh=False) -> dict[str, str]:
    global _credentials
    if _credentials is not None and not force_refresh:
        return _credentials

    sts = boto3.client("sts")
    res = sts.assume_role(
        RoleArn=os.environ["SamsaraFunctionExecRoleArn"],
        RoleSessionName=os.environ["SamsaraFunctionName"],
    )
    _credentials = {
        "aws_access_key_id": res["Credentials"]["AccessKeyId"],
        "aws_secret_access_key": res["Credentials"]["SecretAccessKey"],
        "aws_session_token": res["Credentials"]["SessionToken"],
    }
    return _credentials


_storage: None | Storage = None


def get_storage() -> Storage:
    global _storage
    if _storage is not None:
        return _storage
    _storage = Storage(get_credentials())
    return _storage


_databases: dict[str, Database] = {}


def get_database(namespace: str | None = None) -> Database:
    if namespace is None:
        namespace = os.environ["SamsaraFunctionName"]
    global _databases
    if namespace in _databases:
        return _databases[namespace]
    _databases[namespace] = Database(get_storage(), namespace)
    return _databases[namespace]
