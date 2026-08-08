"""ORM 모델.

카드는 중첩이 깊고 context 의 키가 원본 라벨 그대로라 과정마다 다르다.
전부 관계형으로 펼치면 새 양식이 올 때마다 스키마가 깨지므로,
카드 JSON 을 진실의 원본으로 두고 조회에 쓰이는 값만 컬럼으로 뽑는다.
(계약 원칙: 원본은 고치지 않는다 / 모든 값에 출처를 남긴다)
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Upload(Base):
    __tablename__ = "uploads"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    warnings: Mapped[Any] = mapped_column(JSON, default=list)
    cards: Mapped[list["Card"]] = relationship(back_populates="upload")


class UploadDraft(Base):
    """판정만 끝나고 아직 카드가 되지 않은 업로드 (UI 통합 지점 ①→②).

    UI 는 파일을 올린 뒤 **검증 화면에서 담당자 확인을 받고** 나서야 카드를
    만든다. 그 사이에 판정 결과를 어딘가 붙들고 있어야 하는데, 카드로 만들어
    두면 담당자가 취소했을 때 지워야 할 것이 생긴다. 그래서 확정 전 단계는
    카드가 아니라 여기에 둔다 — 버려도 아무것도 남지 않는다.
    """
    __tablename__ = "upload_drafts"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    status: Mapped[str] = mapped_column(String(16), default="analyzed", index=True)
    # 판정 결과 전문 — POST /uploads/analyze 가 돌려준 그대로
    analysis: Mapped[Any] = mapped_column(JSON, default=dict)
    # 담당자가 확정한 값 (commit 시 기록). 엔진은 이 값을 재판정하지 않는다.
    confirmed: Mapped[Any] = mapped_column(JSON, default=dict, nullable=True)
    upload_id: Mapped[Optional[int]] = mapped_column(ForeignKey("uploads.id"))


class Course(Base):
    """과정 마스터. courseId 발급 주체는 엔진이다 (통합 명세 §5-5 의 답).

    회차(1차·2차)는 여기 두지 않는다. 회차는 카드에 이미 있고, 과정은
    그 카드들을 묶는 이름일 뿐이다. 이 줄이 늘어나는 것은 새 과정이 생길
    때뿐이어야 한다.
    """
    __tablename__ = "courses"
    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    instructor: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    created_by: Mapped[Optional[str]] = mapped_column(String(64))


class CourseAlias(Base):
    """표기 변형 사전.

    같은 과정이 파일마다 '리더십교육' · '리더십 교육' · '리더십 과정' 으로
    들어온다. 담당자가 한 번 "이건 그 과정이다"라고 확정하면 그 표기를 여기
    적어 두고, 다음 업로드부터는 묻지 않는다 (핸드오프 v2 §4-4).
    """
    __tablename__ = "course_aliases"
    id: Mapped[int] = mapped_column(primary_key=True)
    alias: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    course_id: Mapped[str] = mapped_column(String(64), index=True)
    confirmed_by: Mapped[Optional[str]] = mapped_column(String(64))


class Card(Base):
    """개인 1명분 정규화 결과. card_json 이 계약 v0.5 스키마 전문."""
    __tablename__ = "cards"
    id: Mapped[int] = mapped_column(primary_key=True)
    upload_id: Mapped[int] = mapped_column(ForeignKey("uploads.id"), index=True)
    schema_version: Mapped[str] = mapped_column(String(8), default="0.5")

    # --- 조회·필터용 추출 컬럼 (진실의 원본은 card_json) ---
    direction: Mapped[str] = mapped_column(String(32))
    person_name: Mapped[str] = mapped_column(String(64), index=True)
    person_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    person_status: Mapped[str] = mapped_column(String(16), default="regular")
    course_name: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    session_date: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    round_label: Mapped[Optional[str]] = mapped_column(String(32))
    source_type: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    type_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    max_severity: Mapped[Optional[str]] = mapped_column(String(24), index=True)

    card_json: Mapped[Any] = mapped_column(JSON)

    source_file: Mapped[str] = mapped_column(String(255))
    source_sheet: Mapped[str] = mapped_column(String(128))
    source_row: Mapped[Optional[str]] = mapped_column(String(64))

    upload: Mapped["Upload"] = relationship(back_populates="cards")
    report: Mapped[Optional["Report"]] = relationship(back_populates="card",
                                                      uselist=False)


class Handoff(Base):
    """파이썬 규칙이 완결할 수 없는 작업(문장 생성)의 위임 큐.

    status: pending -> returned -> accepted | rejected
    R-16 때문에 근거 없이 돌아온 결과는 accepted 가 될 수 없다.
    """
    __tablename__ = "handoffs"
    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id"), index=True)
    rule_id: Mapped[str] = mapped_column(String(8), index=True)
    task: Mapped[str] = mapped_column(String(64))
    payload: Mapped[Any] = mapped_column(JSON)
    result: Mapped[Any] = mapped_column(JSON, default=None, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    reject_reason: Mapped[Optional[str]] = mapped_column(Text)


class Report(Base):
    """리포트 = 공통 틀 + 성장 섹션(R-14) + 큐레이션 섹션(R-17).

    섹션의 있고 없음은 담당자 임의가 아니라 규칙이 결정한다.
    body 안에 데이터 뷰(sections)와 표현 뷰(presentation)가 함께 들어간다.
    """
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id"), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    body: Mapped[Any] = mapped_column(JSON)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(64))
    reviewed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    sent_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)   # 메일 발송 시각
    card: Mapped["Card"] = relationship(back_populates="report")


class CompetencyMapping(Base):
    """R-18: 승인된 매핑은 저장해 재사용한다 — 데이터 자산화의 기반."""
    __tablename__ = "competency_mappings"
    id: Mapped[int] = mapped_column(primary_key=True)
    raw_name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    canonical: Mapped[str] = mapped_column(String(64))
    approved_by: Mapped[Optional[str]] = mapped_column(String(64))


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
    alias: Mapped[Optional[str]] = mapped_column(String(64))
    email: Mapped[Optional[str]] = mapped_column(String(255))
    department: Mapped[Optional[str]] = mapped_column(String(128))
