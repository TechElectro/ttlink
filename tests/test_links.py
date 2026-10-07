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
        self.ekey_fail = None
        self.cards = {}  # cardId -> (lockId, cardNumber)
        self.deleted_cards = []
        self._next_card = 77

    def remote_unlock(self, lock_id):
        if self.fail:
            raise TTLockError(self.fail, "x")
        self.unlocks += 1

    def send_ekey(self, *a, **k):
        if self.ekey_fail:
            raise TTLockError(self.ekey_fail, "x")
        return 99

    def add_card(self, lock_id, number, name, start_ms, end_ms):
        cid = self._next_card
        self._next_card += 1
        self.cards[cid] = (lock_id, number)
        return cid

    def delete_card(self, lock_id, card_id):
        self.cards.pop(card_id, None)
        self.deleted_cards.append(card_id)

    def list_cards(self, lock_id):
        return [{"cardId": cid, "cardNumber": n} for cid, (lid, n) in self.cards.items() if lid == lock_id]

    def delete_ekey(self, k):
        pass


fake = FakeClient()
main.client = fake
main.settings.unlock_min_interval_s = 0
H = {"X-API-Key": "k"}


def _body():
    now = datetime.now(timezone.utc)
    return {"lock_id": 1, "guest_name": "Ana", "valid_from": (now - timedelta(hours=1)).isoformat(),
            "valid_to": (now + timedelta(hours=1)).isoformat()}


def make(tc, **over):
    return tc.post("/api/accesses", json={**_body(), **over}, headers=H).json()


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


def test_card():
    with TestClient(main.app) as tc:
        a = make(tc, lock_id=5, card_number=" 0012345678\n")
        assert a["card_number"] == "0012345678"
        assert fake.cards[fake._next_card - 1] == (5, "0012345678")

        # misma tarjeta, misma cerradura, estadía vigente -> 409
        r = tc.post("/api/accesses", headers=H, json={**_body(), "lock_id": 5, "card_number": "0012345678"})
        assert r.status_code == 409 and "Ana" in r.json()["detail"]

        # revocar borra la tarjeta de la cerradura
        cid = fake._next_card - 1
        tc.delete(f"/api/accesses/{a['id']}", headers=H)
        assert cid in fake.deleted_cards and cid not in fake.cards

        # tarjeta de una estadía revocada que quedó en la cerradura: se borra y se re-registra
        b = make(tc, lock_id=5, card_number="0012345678")
        old = fake._next_card - 1
        fake.deleted_cards.clear()
        main_db = next(main.get_db())
        main_db.get(main.Access, b["id"]).revoked = True
        main_db.commit()
        c = make(tc, lock_id=5, card_number="0012345678")
        assert old in fake.deleted_cards and c["card_number"] == "0012345678"
        tc.delete(f"/api/accesses/{c['id']}", headers=H)

        # tarjeta registrada fuera de TTLINK (p. ej. de personal): no se toca -> 409
        fake.cards[500] = (5, "5550001")
        r = tc.post("/api/accesses", headers=H, json={**_body(), "lock_id": 5, "card_number": "5550001"})
        assert r.status_code == 409 and "fuera del sistema" in r.json()["detail"]
        assert 500 in fake.cards

        # la misma tarjeta guardada en la cerradura con los bytes invertidos también se detecta
        fake.cards[501] = (5, "546164310")  # = 1456377120 invertido (56ce8d20 -> 208dce56)
        r = tc.post("/api/accesses", headers=H, json={**_body(), "lock_id": 5, "card_number": "1456377120"})
        assert r.status_code == 409 and 501 in fake.cards

        # número inválido -> 422
        assert tc.post("/api/accesses", headers=H, json={**_body(), "card_number": "12AB34"}).status_code == 422


def test_card_rollback_when_ekey_fails():
    with TestClient(main.app) as tc:
        before = tc.get("/api/accesses", headers=H).json()
        fake.ekey_fail = -1
        try:
            r = tc.post("/api/accesses", headers=H,
                        json={**_body(), "lock_id": 6, "card_number": "99887766", "receiver_username": "a@b.com"})
        finally:
            fake.ekey_fail = None
        assert r.status_code == 502
        assert not [c for c in fake.cards.values() if c == (6, "99887766")]
        assert len(tc.get("/api/accesses", headers=H).json()) == len(before)


def test_migration_adds_card_columns(tmp_path):
    from sqlalchemy import create_engine
    from app import db as dbmod
    eng = create_engine(f"sqlite:///{tmp_path}/old.db")
    with eng.begin() as c:
        c.exec_driver_sql("CREATE TABLE accesses (id INTEGER PRIMARY KEY, token VARCHAR(64))")
    old, dbmod.engine = dbmod.engine, eng
    try:
        dbmod.init_db()
    finally:
        dbmod.engine = old
    with eng.begin() as c:
        cols = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(accesses)")}
    assert {"card_id", "card_number"} <= cols
