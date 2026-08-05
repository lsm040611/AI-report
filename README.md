# HR AI Report Engine

엑셀 평가지를 넣으면 **개인 피드백 리포트 HTML**이 나오는 백엔드입니다.
데이터 계약 v0.5의 구현체이고, **규칙 ID와 코드가 1:1로 대응**하도록 짜여 있어
계약서를 왼쪽에 두고 코드를 읽을 수 있습니다.

```
평가지.xlsx  →  스키마 인식  →  정제 규칙 19개  →  카드
             →  검수 관문  →  AI 문장 생성(R-16 검사)  →  리포트 HTML
```

---

## 1. 실행

Python 3.10 이상. Node·DB 서버·외부 워크플로 엔진은 필요 없습니다.

```bash
pip install -r requirements.txt

python make_fixtures.py      # 테스트용 평가지 4종 생성
python smoke_test.py         # 전 구간 한 번 돌려보기 → out/*.html

uvicorn main:app --reload    # 서버
```

> Windows에서 `python` 이 PATH에 없으면 `py -3.10` 으로 바꿔 실행하세요.

| 주소 | 내용 |
|---|---|
| `http://127.0.0.1:8000/` | **업로드 화면.** 파일을 올리면 리포트 링크까지 나옵니다 |
| `/docs` | 프론트 팀과 공유할 API 계약서 (자동 생성) |
| `/rules` | 구현된 규칙 19개 — 계약 규칙표와 대조용 |
| `/health` | 현재 모드 확인 |

PDF가 필요하면 리포트를 브라우저로 열고 `Ctrl+P → 대상: PDF로 저장`.

---

## 2. 두 개의 모드

이 엔진은 같은 코드로 두 가지 태도를 갖습니다. 환경변수로만 바뀝니다.

| 환경변수 | 기본 | 의미 |
|---|---|---|
| `ANTHROPIC_API_KEY` | 없음 | 있으면 Claude API로 문장을 생성하고, 없으면 **목(mock) 생성기**로 돌아갑니다 |
| `HR_AUTO_APPROVE` | `1` | `1`이면 검수 관문을 자동 통과(데모), `0`이면 계약대로 담당자 승인 필요(운영) |
| `HR_MODEL` | `claude-opus-5` | 생성 모델 |
| `HR_EFFORT` | `medium` | 생성 깊이 (`low`~`max`) |
| `HR_DB_URL` | `sqlite:///./hr_report.db` | 저장소 |

```bash
# 실제 생성으로 전환
set ANTHROPIC_API_KEY=sk-ant-...        # PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-..."
uvicorn main:app --reload

# 운영 모드(담당자 승인 필수)로 전환
set HR_AUTO_APPROVE=0
```

**키가 없어도 전 구간이 돌아갑니다.** 목 모드에서는 원문을 재배열만 하므로 없는 사실을
만들지 않고, 그렇게 만들어진 문장이 섞인 리포트에는 푸터에 그 사실이 표기됩니다.

---

## 3. 설계 전제

**규칙 코드에는 LLM 호출이 한 줄도 없습니다.** 생성이 필요한 작업은 `handoff` 큐에
쌓이고, `generation/worker.py` **한 파일만** 그 큐를 가져가 Claude API를 호출합니다.
돌아온 문장은 **R-16 검사를 통과해야만** 저장됩니다.

이건 우회가 아니라 계약이 지시한 구조입니다. 계약의 다섯 번째 원칙이 "애매하면 판단하지
않고 사람에게 넘긴다"이고, R-12는 확신도 low를 무조건 `unknown` + 담당자 질문으로
보내라고 명시합니다. 코드는 확신 가능한 구간만 처리하고 나머지는 검수 관문으로 올립니다.

바뀐 것은 **큐를 가져가는 주체**뿐입니다. 외부 자동화(n8n 등)를 쓰고 싶으면
`GET /handoff/pending` → `POST /handoff/{id}/callback` 경로가 그대로 열려 있습니다.

---

## 4. 폴더 구성

```
main.py            FastAPI 진입점 + 업로드 화면
config.py          환경 설정 한 곳 (키 없으면 목 모드로 떨어지는 규칙)
database.py        SQLAlchemy 세션 (SQLite 기본)
models.py          ORM — card_json 이 진실의 원본

pipeline/
  reader.py        엑셀 읽기 — 셀 안의 부분 서식을 의미로 보존 (R-05)
  detect.py        스키마 추론 — 컬럼 위치 하드코딩 없음
  builder.py       카드 조립 + source_type 판정
  rules/
    base.py        severity 4단계 · 검수 게이트 · 규칙 등록부
    structural.py  R-01~06   semantic.py  R-07·12·13·15·18
    survey.py      R-08~10   report.py    R-11·14·16·17·19

generation/
  worker.py        ★ LLM을 호출하는 유일한 파일 (없으면 목 생성기)
  prompts.py       작업별 프롬프트 + 근거를 강제하는 출력 스키마
  runner.py        큐 소진기: pending → returned → accepted

render/
  template.py      리포트 렌더러 (report_template.js 의 파이썬 이식본)
  adapter.py       ★ 정규화 카드 → 표현 카드. 두 계층을 잇는 지점

routers/           uploads · cards · handoff · reports
make_fixtures.py   테스트용 평가지 4종 생성
smoke_test.py      전 구간 점검 → out/*.html
_backup_flat/      정리 전 원본 파일 보관 (참고용, 실행에 쓰이지 않음)
```

---

## 5. 규칙 → 코드 대응

| 규칙 | 담당 | 구현 위치 | 상태 |
|---|---|---|---|
| R-01 날짜 정규화 | code | `rules/structural.py` | ✅ |
| R-02 점수 캐스팅 | code | `rules/structural.py` | ✅ |
| R-03 / R-03b 평균 재계산 | code | `rules/structural.py` | ✅ |
| R-04 척도 부착 | code | `rules/structural.py` | ✅ |
| R-05 강조 서식 → 의미 | code | `reader.py` + `structural.py` | ✅ |
| R-06 행 유효성 | code | `rules/structural.py` | ✅ |
| R-07 비정규 참가자 | code+ai+human | `rules/semantic.py` | ✅ hold로 사람에게 |
| R-08 문항정의 조인 | code | `rules/survey.py` + `detect.py` | ✅ |
| R-09 응답 집계 | code | `rules/survey.py` | ✅ |
| R-10 익명성 판정 | code | `rules/survey.py` | ✅ |
| R-11 익명화 재작성 | code+ai+human | `rules/report.py` → `generation/` | ✅ 소거+재작성 |
| R-12 역할 판별 | ai | `rules/semantic.py` | ✅ 확신도 3단계 |
| R-13 번역 | code+ai | `rules/semantic.py` → `generation/` | ✅ 원어 잠금+번역 |
| R-14 회차 간 성장 | code | `rules/report.py` | ✅ |
| R-15 동명이인 | code+human | `rules/semantic.py` | ✅ |
| R-16 생성물 충실성 | ai+human | `rules/report.py` | ✅ 저장 전 검사 |
| R-17 큐레이션 | ai | `rules/report.py` → `generation/` | ✅ |
| R-18 역량 매핑 | ai+human | `rules/semantic.py` → `generation/` | ✅ 사전+승인+저장 |
| R-19 Best Practice | ai+human | `rules/report.py` → `generation/` | ✅ |

문장 생성이 필요한 다섯(R-11·13·17·18·19)은 여전히 handoff로 나가지만, 이제 그 큐를
가져가는 워커가 안에 있어 **파이프라인이 끝까지 이어집니다.**

---

## 6. 검수 관문 (severity 4단계)

| severity | 게이트 동작 |
|---|---|
| `notice` | 통과. 사유만 기록 |
| `review` | 통과하되 담당자 확인 권장 |
| `hold` | **진행 차단.** 승인 또는 제외해야 다음 단계 |
| `block_direct_quote` | 발송은 가능하되 **원문 인용 경로 차단**, 요약본만 |

`is_sendable()` 하나가 이 판정을 담당하고, 리포트 생성·발송 매핑표 양쪽에서 같은
함수를 부릅니다. 자동 모드는 이 잠금을 없앤 것이 아니라 **여는 주체를 사람에서 설정값으로
바꾼 것**이며, 누가 열었는지가 플래그에 그대로 남습니다.

```json
{ "code": "non_regular_participant", "severity": "hold", "resolved": true,
  "decision": "approve", "resolved_by": "자동 모드",
  "memo": "자동 모드에서 통과시켰습니다. 운영 모드에서는 담당자 승인이 필요합니다." }
```

---

## 7. 표현 계층 (`render/adapter.py`)

백엔드의 카드는 **데이터**고 리포트 템플릿의 카드는 **표현**이라 모양이 다릅니다.
어댑터가 그 간극을 메우며, 세 가지 원칙을 지킵니다.

1. 점수 데이터는 그대로 옮긴다.
2. "가장 잘한 것 / 키울 것" 같은 판단은 여기서 만든다 — 백엔드 어디에도 없는 표현
   계층의 결정이고, 근거는 **점수 비교뿐**이다. 문장을 지어내지 않는다.
3. 문장이 필요한 자리는 **R-16을 통과한 생성물만** 쓴다. 생성물이 없으면 그 섹션을
   만들지 않는다 — 렌더러가 빈 섹션을 지우고 번호를 다시 매긴다.

그래서 사람마다 리포트 섹션 수가 다른 것이 정상입니다.

| | 강지우(2차수) | 서지호(특강) | 한도윤(360진단) |
|---|---|---|---|
| 01 | 이번 회차, 한눈에 | 오늘 특강, 한눈에 | 진단 결과, 한눈에 |
| 02 | 지난 회차 이후 달라진 점 | 역량별 결과 | 누가 어떻게 보고 있는가 |
| 03 | 역량별 결과 | 오늘 잘하신 점 | 함께 일하는 분들이 짚은 강점 |
| 04 | 이번에 통한 것 | 함께 살펴보면 좋을 점 | 함께 일하는 분들이 바라는 변화 |
| 05 | 함께 살펴보면 좋을 점 | 다음까지 해 볼 것 | 다음까지 해 볼 것 |
| 06 | 나의 표현 교정 노트 | | |
| 07 | 다음까지 해 볼 것 | | |

디자인은 확정 시안(`리포트_시안_v2.html`)과 동일합니다. 색을 바꾸려면
`render/template.py` 의 `CSS` 안 `:root` 변수만 고치면 전체에 반영됩니다.

---

## 8. 처음 보는 양식

`detect.py` 는 컬럼 위치를 하드코딩하지 않습니다.

- 헤더 행: 문자열이 가장 촘촘하고 아래에 데이터가 있는 행
- **값의 성질을 라벨보다 먼저 봅니다.** 숫자 비율 80% 이상이면 점수 열이고,
  라벨 힌트는 그 다음입니다. (역량명 `이해관계 파악` 이 "관계" 때문에 관계 열로
  오인돼 통째로 사라지던 문제를 이 순서로 고쳤습니다.)
- 평균·합계 열은 역량이 아니라 **원본 평균**으로 따로 빼서 R-03 비교에만 씁니다
- 서술 열: 평균 글자 수 기준 / 이름 열: 라벨 힌트 → 실패 시 짧은 문자열 열을 medium
- 방향: 관계 열 + 이름 반복 → `aggregated_responses`
- 문항정의 시트: 시트 이름이 아니라 `Q1·Q2…` 로 시작하는 열이 있는지로 찾습니다

판별 실패는 예외를 던지지 않고 `unknown` + `warnings` 로 남겨 담당자에게 올립니다.

---

## 9. 저장 전략

카드는 중첩이 깊고 `context` 의 키가 원본 라벨 그대로라 과정마다 다릅니다.
전부 관계형으로 펼치면 새 양식이 올 때마다 스키마가 깨지므로, `card_json` 을 진실의
원본으로 두고 조회·필터에 쓰는 값만 컬럼으로 뽑았습니다. 계약 원칙 "원본은 고치지
않는다"의 저장 계층 버전입니다.

---

## 10. 아직 비어 있는 것

- **명부** — 제공 데이터가 더미라 사번·이메일이 없습니다. `RosterEntry` 에 운영
  입력물로 받도록 자리만 만들어 두었고, 없으면 `roster_missing`(notice)이 붙습니다.
  명부가 없으면 R-15 동명이인 판정도 못 합니다.
- **실제 발송** — 계약상 범위 밖. `/reports/dispatch-table/list` 까지만.
- **인증** — 없습니다. 역할 구분은 프론트가 처리하고 백엔드는 조회 키만 받습니다.
- **동명이인 handoff 매칭** — 같은 업로드에 동명이인이 있으면 생성 작업이 첫 카드로
  몰립니다. person_id 가 붙기 전까지는 이름이 유일한 키라서 생기는 한계입니다.
- **PDF 서버 렌더링** — 지금은 브라우저 인쇄가 전제입니다. 서버에서 뽑으려면
  headless Chrome/Playwright 를 붙이고 그 서버에 한글 폰트를 설치해야 합니다.
