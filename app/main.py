import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db, init_db
from .models import Access, UnlockLog, utcnow
from .ttlock_client import TTLockError, client

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="TTLINK")


@app.on_event("startup")
def _startup():
    init_db()


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _ms(dt: datetime) -> int:
    return int(_aware(dt).timestamp() * 1000)


def require_admin(x_api_key: str = Header(default="")):
    if not secrets.compare_digest(x_api_key, settings.admin_api_key):
        raise HTTPException(401, "invalid api key")


def link_state(a: Access) -> str:
    now = utcnow()
    if a.revoked:
        return "revoked"
    if now < _aware(a.valid_from):
        return "not_started"
    if now > _aware(a.valid_to):
        return "expired"
    if a.max_unlocks and a.unlock_count >= a.max_unlocks:
        return "exhausted"
    return "active"


# ---------- admin ----------

class AccessIn(BaseModel):
    lock_id: int
    guest_name: str
    guest_contact: str = ""
    room_label: str = ""
    valid_from: datetime
    valid_to: datetime
    max_unlocks: int = 0
    receiver_username: str | None = None  # si viene, se envía eKey remota


def _out(a: Access) -> dict:
    return {
        "id": a.id, "url": f"{settings.public_base_url}/o/{a.token}", "lock_id": a.lock_id,
        "guest_name": a.guest_name, "room_label": a.room_label, "ekey_id": a.ekey_id,
        "valid_from": a.valid_from, "valid_to": a.valid_to, "max_unlocks": a.max_unlocks,
        "unlock_count": a.unlock_count, "state": link_state(a),
    }


@app.post("/api/accesses", dependencies=[Depends(require_admin)])
def create_access(body: AccessIn, db: Session = Depends(get_db)):
    if _aware(body.valid_to) <= _aware(body.valid_from):
        raise HTTPException(422, "valid_to must be after valid_from")
    a = Access(
        lock_id=body.lock_id, guest_name=body.guest_name, guest_contact=body.guest_contact,
        room_label=body.room_label, valid_from=body.valid_from, valid_to=body.valid_to,
        max_unlocks=body.max_unlocks,
    )
    if body.receiver_username:
        try:
            a.ekey_id = client.send_ekey(
                body.lock_id, body.receiver_username, f"{body.guest_name}",
                _ms(body.valid_from), _ms(body.valid_to),
            )
        except TTLockError as e:
            raise HTTPException(502, str(e))
    db.add(a)
    db.commit()
    return _out(a)


@app.get("/api/accesses", dependencies=[Depends(require_admin)])
def list_accesses(db: Session = Depends(get_db)):
    return [_out(a) for a in db.scalars(select(Access).order_by(Access.id.desc()))]


@app.delete("/api/accesses/{access_id}", dependencies=[Depends(require_admin)])
def revoke_access(access_id: int, db: Session = Depends(get_db)):
    a = db.get(Access, access_id)
    if not a:
        raise HTTPException(404)
    if a.ekey_id:
        try:
            client.delete_ekey(a.ekey_id)
        except TTLockError as e:
            raise HTTPException(502, str(e))
    a.revoked = True
    db.commit()
    return _out(a)


@app.get("/api/accesses/{access_id}/logs", dependencies=[Depends(require_admin)])
def access_logs(access_id: int, db: Session = Depends(get_db)):
    rows = db.scalars(select(UnlockLog).where(UnlockLog.access_id == access_id).order_by(UnlockLog.id.desc()))
    return [{"ts": r.ts, "ip": r.ip, "ok": r.ok, "errcode": r.errcode} for r in rows]


@app.get("/api/locks", dependencies=[Depends(require_admin)])
def locks():
    try:
        return client.list_locks()
    except TTLockError as e:
        raise HTTPException(502, str(e))


@app.get("/api/locks/{lock_id}", dependencies=[Depends(require_admin)])
def lock_detail(lock_id: int):
    try:
        return client.get_lock_detail(lock_id)
    except TTLockError as e:
        raise HTTPException(502, str(e))


# ---------- público ----------

def _by_token(db: Session, token: str) -> Access:
    a = db.scalar(select(Access).where(Access.token == token))
    if not a:
        raise HTTPException(404, "link not found")
    return a


@app.get("/o/{token}", include_in_schema=False)
def open_page(token: str):
    return FileResponse(STATIC / "open.html")


@app.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(STATIC / "admin.html")


@app.get("/api/o/{token}")
def link_info(token: str, db: Session = Depends(get_db)):
    a = _by_token(db, token)
    return {
        "hotel": settings.hotel_name, "guest_name": a.guest_name, "room_label": a.room_label,
        "valid_from": a.valid_from, "valid_to": a.valid_to, "state": link_state(a),
    }


ERRORS = {
    -4043: "La apertura remota no está habilitada en esta cerradura.",
    -3003: "La cerradura no está disponible. Intente acercándose a la puerta.",
    -2012: "La cerradura no está conectada al gateway.",
}


@app.post("/api/o/{token}/unlock")
def unlock(token: str, request: Request, db: Session = Depends(get_db)):
    a = _by_token(db, token)
    state = link_state(a)
    if state != "active":
        raise HTTPException(403, state)
    now = utcnow()
    if a.last_attempt_at and (now - _aware(a.last_attempt_at)).total_seconds() < settings.unlock_min_interval_s:
        raise HTTPException(429, "too_fast")
    a.last_attempt_at = now
    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")[:300]
    try:
        client.remote_unlock(a.lock_id)
    except TTLockError as e:
        db.add(UnlockLog(access_id=a.id, ip=ip, user_agent=ua, ok=False, errcode=e.errcode))
        db.commit()
        raise HTTPException(502, ERRORS.get(e.errcode, "No se pudo abrir la puerta. Contacte a recepción."))
    a.unlock_count += 1
    db.add(UnlockLog(access_id=a.id, ip=ip, user_agent=ua, ok=True))
    db.commit()
    return {"ok": True}
