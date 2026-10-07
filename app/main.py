import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
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
    card_number: str | None = None  # si viene, se registra la tarjeta RFID por gateway

    @field_validator("card_number")
    @classmethod
    def _card(cls, v: str | None) -> str | None:
        # El lector USB escribe como teclado: puede traer espacios o Enter
        v = "".join((v or "").split())
        if not v:
            return None
        if not v.isdigit() or not 4 <= len(v) <= 20:
            raise ValueError("Número de tarjeta no válido")
        return v


def _card_error(e: TTLockError) -> str:
    return ERRORS.get(e.errcode, f"TTLock no pudo registrar la tarjeta: {e.errmsg} ({e.errcode})")


def _free_card(db: Session, lock_id: int, number: str) -> None:
    """Deja la tarjeta lista para asignarse: falla si la usa una estadía vigente o si
    fue registrada fuera de TTLINK (p. ej. tarjeta de personal), y la borra de la
    cerradura solo si quedó de una estadía nuestra ya vencida/revocada."""
    live = ("active", "not_started")
    for other in db.scalars(select(Access).where(Access.lock_id == lock_id, Access.card_number == number)):
        if link_state(other) in live:
            raise HTTPException(409, _card_taken(other))
    try:
        stale = [c for c in client.list_cards(lock_id) if c.get("cardNumber") == number]
        for c in stale:
            owner = db.scalar(select(Access).where(Access.card_id == c["cardId"]))
            if not owner:
                raise HTTPException(409, f"Esta tarjeta ya está registrada en la cerradura fuera del sistema "
                                         f"(\"{c.get('cardName') or c['cardId']}\"). Use otra tarjeta o bórrela "
                                         f"desde la app TTLock.")
            if link_state(owner) in live:
                raise HTTPException(409, _card_taken(owner))
            client.delete_card(lock_id, c["cardId"])
    except TTLockError as e:
        raise HTTPException(502, _card_error(e))


def _card_taken(a: Access) -> str:
    return f"Esta tarjeta ya está asignada a {a.guest_name}" + (f", hab. {a.room_label}" if a.room_label else "") + "."


def _out(a: Access) -> dict:
    return {
        "id": a.id, "url": f"{settings.public_base_url}/o/{a.token}", "lock_id": a.lock_id,
        "guest_name": a.guest_name, "room_label": a.room_label, "ekey_id": a.ekey_id,
        "card_number": a.card_number,
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
    if body.card_number:
        _free_card(db, body.lock_id, body.card_number)
        name = body.guest_name + (f" hab. {body.room_label}" if body.room_label else "")
        try:
            a.card_id = client.add_card(body.lock_id, body.card_number, name[:50],
                                        _ms(body.valid_from), _ms(body.valid_to))
        except TTLockError as e:
            raise HTTPException(502, _card_error(e))
        a.card_number = body.card_number
    if body.receiver_username:
        try:
            a.ekey_id = client.send_ekey(
                body.lock_id, body.receiver_username, f"{body.guest_name}",
                _ms(body.valid_from), _ms(body.valid_to),
            )
        except TTLockError as e:
            # No dejar en la cerradura una tarjeta de una llave que no se guardó
            if a.card_id:
                try:
                    client.delete_card(body.lock_id, a.card_id)
                except TTLockError:
                    pass
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
    if a.card_id:
        try:
            client.delete_card(a.lock_id, a.card_id)
        except TTLockError as e:
            # Si ya no está en la cerradura (borrada a mano), se puede seguir
            try:
                still = any(c.get("cardId") == a.card_id for c in client.list_cards(a.lock_id))
            except TTLockError:
                still = True
            if still:
                raise HTTPException(502, _card_error(e))
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
