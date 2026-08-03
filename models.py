"""ORM 모델.

카드는 중첩이 깊고 context 의 키가 원본 라벨 그대로라 과정마다 다르다.
전부 관계형으로 펼치면 새 양식이 올 때마다 스키마가 깨지므로,
카드 JSON 을 진실의 원본으로 두고 조회에 쓰이는 값만 컬럼으로 뽑는다.
(계약 원칙: 원본은 고치지 않는다 / 모든 값에 출처를 남긴다)
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (JSON, Boolean, DateTime, ForeignKey, Integer, String,
                        Text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Upload(Base):
    __tablename__ = "uploads"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    cards: Mapped[list["Card"]] = relationship(back_populates="upload")


class Card(Base):
    """개인 1명분 정규화 결과. card_json 이 계약 v0.5 스키마 전문."""
    __tablename__ = "cards"
    id: Mapped[int] = mapped_column(primary_key=True)
    upload_id: Mapped[int] = mapped_column(ForeignKey("uploads.id"))
    schema_version: Mapped[str] = mapped_column(String(8), default="0.5")

    # --- 조회·필터용 추출 컬럼 (진실의 원본은 card_json) ---
    direction: Mapped[str] = mapped_column(String(32))
    person_name: Mapped[str] = mapped_column(String(64), index=True)
    person_id: Mapped[str | None] = mapped_column(String(32), index=True)
    person_status: Mapped[str] = mapped_column(String(16), default="regular")
    course_name: Mapped[str | None] = mapped_column(String(255), index=True)
    session_date: Mapped[str | None] = mapped_column(String(10), index=True)
    round_label: Mapped[str | None] = mapped_column(String(32))
    source_type: Mapped[str | None] = mapped_column(String(32), index=True)
    type_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    max_severity: Mapped[str | None] = mapped_column(String(24), index=True)

    card_json: Mapped[dict] = mapped_column(JSON)

    source_file: Mapped[str] = mapped_column(String(255))
    source_sheet: Mapped[str] = mapped_column(String(128))
    source_row: Mapped[str | None] = mapped_column(String(64))

    upload: Mapped[Upload] = relationship(back_populates="cards")
    report: Mapped["Report | None"] = relationship(back_populates="card", uselist=False)


class Handoff(Base):
    """파이썬이 완결할 수 없는 작업(문장 생성)의 위임 큐.

    status: pending -> returned -> accepted | rejected
    R-16 때문에 evidence 없이 돌아온 결과는 accepted 가 될 수 없다.
    """
    __tablename__ = "handoffs"
    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id"))
    rule_id: Mapped[str] = mapped_column(String(8), index=True)
    task: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON, default=None)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    reject_reason: Mapped[str | None] = mapped_column(Text)


class Report(Base):
    """리포트 = 공통 틀 + 성장 섹션(R-14) + 큐레이션 섹션(R-17).

    섹션의 있고 없음은 담당자 임의가 아니라 규칙이 결정한다.
    """
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id"), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    body: Mapped[dict] = mapped_column(JSON)
    reviewed_by: Mapped[str | None] = mapped_column(String(64))
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    card: Mapped[Card] = relationship(back_populates="report")


class CompetencyMapping(Base):
    """R-18: 승인된 매핑은 저장해 재사용한다 — 데이터 자산화의 기반."""
    __tablename__ = "competency_mappings"
    id: Mapped[int] = mapped_column(primary_key=True)
    raw_name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    canonical: Mapped[str] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(String(64))


class PersonResolution(Base):
    """R-15: 담당자에게 물은 동명이인 판단을 기억한다."""
    __tablename__ = "person_resolutions"
    id: Mapped[int] = mapped_column(primary_key=True)
    memo_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    person_id: Mapped[str] = mapped_column(String(32))


class RosterEntry(Base):
    """운영 입력물. 제공 데이터가 더미라 사번·이메일이 없어 별도로 받는다."""
    __tablename__ = "roster"
    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    alias: Mapped[str | None] = mapped_column(String(64))
    email: Mapped[str | None] = mapped_column(String(255))
    department: Mapped[str | None] = mapped_column(String(128))
