import secrets
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Access(Base):
    __tablename__ = "accesses"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, default=lambda: secrets.token_urlsafe(32))
    lock_id: Mapped[int] = mapped_column(Integer)
    guest_name: Mapped[str] = mapped_column(String(120))
    guest_contact: Mapped[str] = mapped_column(String(120), default="")
    room_label: Mapped[str] = mapped_column(String(60), default="")
    ekey_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    max_unlocks: Mapped[int] = mapped_column(Integer, default=0)  # 0 = sin límite
    unlock_count: Mapped[int] = mapped_column(Integer, default=0)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UnlockLog(Base):
    __tablename__ = "unlock_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    access_id: Mapped[int] = mapped_column(ForeignKey("accesses.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    ok: Mapped[bool] = mapped_column(Boolean)
    errcode: Mapped[int] = mapped_column(Integer, default=0)
