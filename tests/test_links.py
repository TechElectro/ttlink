import os
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["ADMIN_API_KEY"] = "k"

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.ttlock_client import TTLockError  # noqa: E402


class FakeClient:
    def __init__(self):
        self.fail = None
        self.unlocks = 0

    def remote_unlock(self, lock_id):
        if self.fail:
            raise TTLockError(self.fail, "x")
        self.unlocks += 1

    def send_ekey(self, *a, **k):
        return 99

    def delete_ekey(self, k):
        pass


fake = FakeClient()
main.client = fake
main.settings.unlock_min_interval_s = 0
H = {"X-API-Key": "k"}


def make(tc, **over):
    now = datetime.now(timezone.utc)
    body = {"lock_id": 1, "guest_name": "Ana", "valid_from": (now - timedelta(hours=1)).isoformat(),
            "valid_to": (now + timedelta(hours=1)).isoformat(), **over}
    return tc.post("/api/accesses", json=body, headers=H).json()


def test_flow():
    with TestClient(main.app) as tc:
        a = make(tc, receiver_username="a@b.com", max_unlocks=1)
        assert a["ekey_id"] == 99
        tok = a["url"].rsplit("/", 1)[1]
        assert tc.post(f"/api/o/{tok}/unlock").status_code == 200
        assert tc.post(f"/api/o/{tok}/unlock").status_code == 403  # agotado
        b = make(tc)
        tok2 = b["url"].rsplit("/", 1)[1]
        fake.fail = -4043
        assert tc.post(f"/api/o/{tok2}/unlock").status_code == 502
        fake.fail = None
        tc.delete(f"/api/accesses/{b['id']}", headers=H)
        assert tc.post(f"/api/o/{tok2}/unlock").status_code == 403  # revocado
        assert tc.get("/api/accesses").status_code == 401


def test_expired():
    with TestClient(main.app) as tc:
        now = datetime.now(timezone.utc)
        a = make(tc, valid_from=(now - timedelta(days=2)).isoformat(), valid_to=(now - timedelta(days=1)).isoformat())
        tok = a["url"].rsplit("/", 1)[1]
        assert tc.get(f"/api/o/{tok}").json()["state"] == "expired"
