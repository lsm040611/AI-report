# HR AI Report Engine — 백엔드

데이터 계약 v0.5의 구현체. **규칙 ID와 코드가 1:1로 대응**하도록 짜여 있어서,
계약서를 왼쪽에 두고 코드를 읽을 수 있다.

## 실행

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

`http://127.0.0.1:8000/docs` — 프론트 팀과 공유할 API 계약서가 자동 생성된다.
`http://127.0.0.1:8000/rules` — 구현된 규칙 목록. 계약 규칙표와 대조용.

## 설계 전제

**파이썬 코드에는 LLM 호출이 한 줄도 없다.** 생성이 필요한 작업은
`handoff` 큐에 쌓았다가 외부 자동화(n8n 등)가 가져가고, 결과를 콜백으로
되돌려받는다. 되돌아온 문장은 R-16 검사를 통과해야만 저장된다.

이건 우회가 아니라 계약이 지시한 구조다. 계약의 다섯 번째 원칙이
"애매하면 판단하지 않고 사람에게 넘긴다"이고, R-12는 확신도 low를
무조건 `unknown` + 담당자 질문으로 보내라고 명시한다. 코드는 확신
가능한 구간만 처리하고 나머지는 검수 관문으로 올린다.

## 파이프라인

```
업로드
  └ reader.py    엑셀 읽기 — 서식을 의미로 보존 (R-05)
  └ detect.py    스키마 추론 — 컬럼 위치 하드코딩 없음
  └ builder.py   카드 조립 + source_type 판정
  └ rules/       정제 규칙 19개
       ↓
source_type 승인  (confirmed_by_operator = true 여야 진행)
       ↓
검수 관문         (hold 미해결이면 진행 불가)
       ↓
리포트 생성       공통 틀 + 성장(R-14) + 큐레이션(R-17)
       ↓
발송 매핑표       실제 발송은 프로토타입 범위 밖
```

## 규칙 → 코드 대응

| 규칙 | 담당 | 구현 위치 | 코드 완결 |
|---|---|---|---|
| R-01 날짜 정규화 | code | `rules/structural.py` | ✅ |
| R-02 점수 캐스팅 | code | `rules/structural.py` | ✅ |
| R-03 / R-03b 평균 재계산 | code | `rules/structural.py` | ✅ |
| R-04 척도 부착 | code | `rules/structural.py` | ✅ |
| R-05 강조 서식 → 의미 | code | `reader.py` + `structural.py` | ✅ |
| R-06 행 유효성 | code | `rules/structural.py` | ✅ |
| R-07 비정규 참가자 | code+ai+human | `rules/semantic.py` | ✅ (hold로 사람에게) |
| R-08 문항정의 조인 | code | `rules/survey.py` | ✅ |
| R-09 응답 집계 | code | `rules/survey.py` | ✅ |
| R-10 익명성 판정 | code | `rules/survey.py` | ✅ |
| R-11 익명화 재작성 | code+ai+human | `rules/report.py` | ◐ 단서 소거만 |
| R-12 역할 판별 | ai | `rules/semantic.py` | ✅ (확신도 3단계) |
| R-13 번역 | code+ai | `rules/semantic.py` | ◐ 원어 잠금만 |
| R-14 회차 간 성장 | code | `rules/report.py` | ✅ |
| R-15 동명이인 | code+human | `rules/semantic.py` | ✅ |
| R-16 생성물 충실성 | ai+human | `rules/report.py` | ✅ (검사자 역할) |
| R-17 큐레이션 | ai | `rules/report.py` | ◐ 근거 수집만 |
| R-18 역량 매핑 | ai+human | `rules/semantic.py` | ✅ (사전+승인) |
| R-19 Best Practice | ai+human | `rules/report.py` | ◐ 그룹 추출만 |

◐ 넷은 문장 생성이 필요해 handoff로 넘어간다. 나머지 15개는 코드에서 완결된다.

## 검수 관문 (severity 4단계)

| severity | 게이트 동작 |
|---|---|
| `notice` | 통과. 리포트에 사유만 자동 표기 |
| `review` | 통과하되 담당자 확인 권장 |
| `hold` | **진행 차단.** 승인 또는 제외해야 다음 단계 |
| `block_direct_quote` | 발송은 가능하되 **원문 인용 경로 차단**, 요약본만 |

`is_sendable()` 하나가 이 판정을 담당하고, 리포트 생성·발송 매핑표
양쪽에서 같은 함수를 부른다.

## 저장 전략

카드는 중첩이 깊고 `context`의 키가 원본 라벨 그대로라 과정마다 다르다.
전부 관계형으로 펼치면 새 양식이 올 때마다 스키마가 깨지므로,
`card_json`을 진실의 원본으로 두고 조회·필터에 쓰는 값만 컬럼으로 뽑았다.
계약 원칙 "원본은 고치지 않는다"의 저장 계층 버전이다.

## 처음 보는 양식

`detect.py`는 컬럼 위치를 하드코딩하지 않는다.

- 헤더 행: 문자열이 가장 촘촘하고 아래에 데이터가 있는 행
- 점수 열: 값의 80% 이상이 숫자 → 분포로 척도까지 추론
- 서술 열: 평균 글자 수 기준
- 이름 열: 라벨 힌트 → 실패 시 짧은 문자열 열을 medium 확신도로
- 방향: 관계 열 + 이름 반복 → `aggregated_responses`

판별 실패는 예외를 던지지 않고 `unknown` + `warnings`로 남겨 담당자에게 올린다.

## 아직 비어 있는 것

- **명부** — 제공 데이터가 더미라 사번·이메일이 없다. `RosterEntry`에
  운영 입력물로 받도록 자리만 만들어 뒀고, 없으면 `roster_missing`(notice).
- **실제 발송** — 계약상 범위 밖. `/reports/dispatch-table`까지만.
- **인증** — 없음. 역할 구분은 프론트가 처리하고 백엔드는 조회 키만 받는다.
