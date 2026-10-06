import time

import httpx

from .config import settings


class TTLockError(Exception):
    def __init__(self, errcode: int, errmsg: str):
        super().__init__(f"TTLock error {errcode}: {errmsg}")
        self.errcode = errcode
        self.errmsg = errmsg


def _now_ms() -> int:
    return int(time.time() * 1000)


class TTLockClient:
    def __init__(self, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(base_url=settings.ttlock_api_base, timeout=20, transport=transport)
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at = 0.0

    def _check(self, data: dict) -> dict:
        if data.get("errcode", 0) != 0:
            raise TTLockError(data["errcode"], data.get("errmsg", ""))
        return data

    def _token(self) -> str:
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        if self._refresh_token:
            payload = {"grant_type": "refresh_token", "refresh_token": self._refresh_token}
        else:
            payload = {
                "grant_type": "password",
                "username": settings.ttlock_username,
                "password": settings.ttlock_password_md5,
            }
        payload.update(client_id=settings.ttlock_client_id, client_secret=settings.ttlock_client_secret)
        data = self._http.post("/oauth2/token", data=payload).json()
        if "access_token" not in data:
            self._refresh_token = None
            raise TTLockError(data.get("errcode", -1), data.get("errmsg", "token request failed"))
        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token")
        self._expires_at = time.time() + int(data.get("expires_in", 7776000))
        return self._access_token

    def _call(self, path: str, **params) -> dict:
        payload = {
            "clientId": settings.ttlock_client_id,
            "accessToken": self._token(),
            "date": _now_ms(),
            **params,
        }
        return self._check(self._http.post(path, data=payload).json())

    def list_locks(self) -> list[dict]:
        return self._call("/v3/lock/list", pageNo=1, pageSize=100).get("list", [])

    def get_lock_detail(self, lock_id: int) -> dict:
        return self._call("/v3/lock/detail", lockId=lock_id)

    def send_ekey(self, lock_id: int, receiver: str, name: str, start_ms: int, end_ms: int,
                  remarks: str = "", create_user: bool = True) -> int:
        data = self._call(
            "/v3/key/send", lockId=lock_id, receiverUsername=receiver, keyName=name,
            startDate=start_ms, endDate=end_ms, remarks=remarks,
            remoteEnable=1, createUser=1 if create_user else 2,
        )
        return data["keyId"]

    def delete_ekey(self, key_id: int) -> None:
        self._call("/v3/key/delete", keyId=key_id)

    def remote_unlock(self, lock_id: int) -> None:
        self._call("/v3/lock/unlock", lockId=lock_id)


client = TTLockClient()
