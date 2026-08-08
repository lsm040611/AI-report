"""SQLAlchemy 세션. SQLite 기본이라 별도 설치 없이 바로 돌아간다."""
from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
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


def ensure_columns() -> list:
    """모델에 새로 생긴 열을 기존 표에 덧붙인다.

    `create_all()` 은 **없는 표만** 만든다. 이미 있는 표에 열이 늘어난 것은
    모른 척한다. 그래서 모델에 컬럼 하나를 추가하고 배포하면, 새 DB 에서는
    잘 돌고 기존 DB 에서는 `no such column` 으로 죽는다. 내 컴퓨터에서 되고
    서버에서 안 되는 전형적인 경우다.

    여기서는 **더하기만** 한다. 이름을 바꾸거나 지우거나 타입을 바꾸는 일은
    하지 않는다 — 그건 자료를 잃을 수 있어 사람이 판단할 일이다.
    """
    insp = inspect(engine)
    added = []
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                kind = col.type.compile(dialect=engine.dialect)
                # NOT NULL 은 붙이지 않는다. 이미 있는 행에 채울 값이 없다.
                sql = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {kind}'
                default = getattr(col.default, "arg", None)
                if isinstance(default, (str, int, float, bool)):
                    lit = f"'{default}'" if isinstance(default, str) else str(default)
                    sql += f" DEFAULT {lit}"
                conn.execute(text(sql))
                added.append(f"{table.name}.{col.name}")
    if added:
        print("[DB] 열을 덧붙였습니다 — " + ", ".join(added))
    return added
