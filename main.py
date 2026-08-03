"""HR AI Report Engine — 백엔드 진입점.

파이프라인:
    업로드 -> 스키마 인식 -> 정제(규칙 19개) -> 카드
          -> source_type 승인 -> 검수 관문(flags) -> 리포트 -> 발송 매핑표
"""
from fastapi import FastAPI

from database import Base, engine
from routers import cards, handoff, reports, uploads

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="HR AI Report Engine",
    version="0.5",
    description="데이터 계약 v0.5 구현. 규칙 ID와 코드가 1:1로 대응합니다.",
)

app.include_router(uploads.router)
app.include_router(cards.router)
app.include_router(handoff.router)
app.include_router(reports.router)


@app.get("/rules")
def rules():
    """구현된 정제 규칙 목록. 계약 규칙표와 대조하기 위한 엔드포인트."""
    from pipeline.rules import REGISTRY
    return [{
        "id": r.rule_id, "owner": r.owner,
        "problem": r.problem, "policy": r.policy,
        "needs_generation": r.needs_generation,
    } for r in sorted(REGISTRY.values(), key=lambda x: x.rule_id)]


@app.get("/health")
def health():
    return {"status": "ok"}
