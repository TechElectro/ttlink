from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# create_all no agrega columnas a tablas existentes: columnas nuevas de accesses
_NEW_COLUMNS = {"card_id": "INTEGER", "card_number": "VARCHAR(32)"}


def init_db():
    from . import models  # noqa: F401
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        have = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(accesses)")}
        for name, sql_type in _NEW_COLUMNS.items():
            if name not in have:
                conn.exec_driver_sql(f"ALTER TABLE accesses ADD COLUMN {name} {sql_type}")
