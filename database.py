"""SQLAlchemy 세션. SQLite 기본이라 별도 설치 없이 바로 돌아간다."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import DB_URL

# SQLite 는 기본적으로 스레드를 넘나드는 커넥션을 막는다. FastAPI 는 워커 스레드에서
# 핸들러를 돌리므로 이 옵션이 필요하다.
_connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}

engine = create_engine(DB_URL, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
