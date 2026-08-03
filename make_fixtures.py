"""계약서의 6개 원본을 흉내낸 테스트 파일 생성 + 파이프라인 검증."""
import sys
sys.path.insert(0, "/home/claude/hr_report_engine/..")

from openpyxl import Workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont

RED = InlineFont(color="FFB3261E")
BOLD_U = InlineFont(b=True, u="single")
BOLD = InlineFont(b=True)


def negotiation():
    wb = Workbook(); ws = wb.active; ws.title = "1차수_A조"
    ws["A1"] = "과정명"; ws["B1"] = "Global Negotiation Program (GN-1)"
    ws["A2"] = "강사"; ws["B2"] = "Rachel Han"
    ws["A3"] = "날짜"; ws["B3"] = 46161          # 시리얼 날짜 (R-01)
    ws["A4"] = "차수"; ws["B4"] = "1차수"
    ws["A6"] = "이름"
    for i, h in enumerate(["Accuracy", "Tone", "Persuasion",
                           "B-1 Strengths", "B-2 Gaps", "B-3 Next Action", "비고"]):
        ws.cell(row=6, column=2 + i, value=h)

    ws["A7"] = "강지우"
    ws["B7"], ws["C7"], ws["D7"] = 4.0, "3.5", 4.5      # C7은 문자열 (R-02)
    ws["E7"] = "오프닝에서 상대 상황을 먼저 요약한 것 아주 좋았음."
    ws["F7"] = CellRichText([
        "가격 인상을 통보하듯 말하는 순간 있었음. ",
        TextBlock(RED, '"We will raise the price"'),
        " → ",
        TextBlock(BOLD_U, '"We may need to adjust our pricing"'),
        " 처럼 ",
        TextBlock(BOLD, "corporate softening"),
        " 필요.",
    ])
    ws["G7"] = "다음 세션까지 hedging 3종 의도적으로 써볼 것."

    ws["A8"] = "문세린"
    ws["B8"], ws["C8"], ws["D8"] = 4.5, 4.5, 3.5
    ws["E8"] = "문법 정확도 조에서 제일 안정적. 관사·시제 오류가 거의 없음."
    ws["F8"] = "정중한데 메시지가 각인이 안 됨. 핵심 요구를 명확히 말한 게 1번."
    ws["G8"] = "결론 먼저. 첫 30초 안에 요구사항 문장이 나오는지 체크하겠음."

    ws["A9"] = "홍민아"
    ws["B9"], ws["C9"], ws["D9"] = 3.5, 3.0, 3.0
    ws["E9"] = "첫 참석. 관찰 위주였지만 마지막에 질문 2개 던진 것 좋았음."
    ws["F9"] = "발화량이 적어 이번 회차는 참고 평가만."
    ws["H9"] = "청강"                                     # R-07

    ws["A11"] = "※ 점수는 5점 만점 기준입니다"            # R-06 각주 행
    wb.save("/home/claude/fixtures/20260519_1차수_A조.xlsx")


def lecture():
    """특강: 1~10 척도, 평균란 없음, 라벨 상이 (R-03b / R-04 / R-12)"""
    wb = Workbook(); ws = wb.active; ws.title = "Presentation특강"
    ws["A1"] = "특강명"; ws["B1"] = "Executive Presentation Skills"
    ws["A2"] = "강사"; ws["B2"] = "Daniel Cho"
    ws["A3"] = "일자"; ws["B3"] = "2026-06-25"
    ws["A4"] = "장소"; ws["B4"] = "사내 교육장"
    ws["A5"] = "성명"
    for i, h in enumerate(["Structure", "Delivery", "Visuals", "Q&A",
                           "Highlights (잘한 점)", "Improvement Areas (보완점)",
                           "Homework (과제)"]):
        ws.cell(row=5, column=2 + i, value=h)
    rows = [
        ("서지호", 8, 7, 9, 6,
         "슬라이드는 오늘 참석자 중 최고. 한 장 한 메시지 원칙 지킴.",
         "Q&A 준비 부족 — 확인해 보겠습니다 3회. 브리지 화법 익힐 것.",
         "예상질문 5개 + 답변 스크립트 작성."),
        ("한예린", 9, 8, 7, 8,
         "오프닝 후킹 굿. 스토리 아크 명확: 문제→시도→실패→전환→결과.",
         "텍스트 위주 슬라이드 2장이 흐름 끊음 → 도식화 필요.",
         "해당 2장 다이어그램으로 리디자인해 제출."),
        ("오태윤", 6, 9, 6, 7,
         "무대 장악력·목소리는 이미 프로급. 발성이 타고난 자산.",
         "나열식이라 결론이 늦게 나옴. 두괄식 필수.",
         "발표 요지 30초 요약 녹음 제출."),
    ]
    for r, row in enumerate(rows, start=6):
        for c, v in enumerate(row, start=1):
            ws.cell(row=r, column=c, value=v)
    wb.save("/home/claude/fixtures/20260625_특강.xlsx")


def survey():
    """360 진단: N응답 -> 1인 (R-09 / R-10)"""
    wb = Workbook(); ws = wb.active; ws.title = "응답데이터"
    ws["A1"] = "진단명"; ws["B1"] = "2026 리더십 360° 진단"
    ws["A3"] = "피평가자"; ws["B3"] = "관계"
    for i, q in enumerate(["Q1", "Q2", "Q3"]):
        ws.cell(row=3, column=3 + i, value=q)
    ws["F3"] = "[주관식1] 이 리더의 가장 큰 강점"
    ws["G3"] = "[주관식2] 이 리더에게 바라는 변화"

    data = [
        ("한도윤", "상사", 5, 3, 4, "방향 제시가 명확하고 실행이 빠릅니다.",
         "속도가 빠른 만큼 실무 의견 수렴이 생략될 때가 있습니다."),
        ("한도윤", "동료", 4, 2, 3, "추진력이 좋습니다.",
         "협업 회의 때 결론을 미리 정해놓고 오시는 느낌이 있습니다."),
        ("한도윤", "구성원", 4, 3, 4, "디렉션이 구체적이라 헤맬 일이 없습니다.",
         "저희 팀에서는 작년 워크숍 때 제가 낸 의견이 그냥 넘어가서 좀 그랬어요ㅠ"),
        ("한도윤", "구성원", 5, 2, 3, "교육 기회를 잘 챙겨주십니다.",
         "1on1이 자주 밀려요ㅠ 세 번 연속 밀리니까 좀 그랬습니다."),
        ("한도윤", "구성원", 4, 3, 4, "전문성이 뛰어납니다.",
         "보고 중간에 말을 끊지 말아주세요."),
    ]
    for r, row in enumerate(data, start=4):
        for c, v in enumerate(row, start=1):
            ws.cell(row=r, column=c, value=v)
    wb.save("/home/claude/fixtures/20260710_360진단.xlsx")


if __name__ == "__main__":
    import os
    os.makedirs("/home/claude/fixtures", exist_ok=True)
    negotiation(); lecture(); survey()
    print("fixtures 생성 완료")
