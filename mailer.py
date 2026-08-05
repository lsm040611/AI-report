"""리포트 메일 발송.

**기본값은 보내지 않는 것이다.** 업로드할 때마다 메일이 나가면 시험 한 번에
실제 사람들에게 피드백이 날아간다. 그래서 두 가지를 모두 만족해야 실제로 나간다.

    1. HR_MAIL_ENABLED=1      (환경변수로 명시적으로 켠다)
    2. 사람이 발송 버튼을 누른다  (업로드만으로는 나가지 않는다)

둘 중 하나라도 빠지면 **미리보기(dry-run)** 로 동작한다 — 누구에게 무엇이 갈지
그대로 돌려주되 실제로 보내지는 않는다. 발표 시연은 이 상태로 하면 된다.

계정 정보는 환경변수로만 받는다. 코드나 저장소에 넣지 않는다.

    $env:HR_SMTP_USER = "myid@naver.com"
    $env:HR_SMTP_PASS = "..."          # 네이버는 '애플리케이션 비밀번호'
    $env:HR_MAIL_ENABLED = "1"
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from typing import Optional

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "off", "")


@dataclass(frozen=True)
class MailConfig:
    host: str
    port: int
    user: str
    password: str
    sender: str
    sender_name: str
    use_ssl: bool
    enabled: bool

    @property
    def ready(self) -> bool:
        """실제 발송에 필요한 것이 다 있는가."""
        return bool(self.enabled and self.host and self.user and self.password)

    def why_not(self) -> str:
        if not self.enabled:
            return "mail.env 의 HR_MAIL_ENABLED 가 꺼져 있습니다"
        missing = [n for n, v in (("HR_SMTP_HOST", self.host),
                                  ("HR_SMTP_USER", self.user),
                                  ("HR_SMTP_PASS", self.password)) if not v]
        if missing:
            return f"mail.env 에 {', '.join(missing)} 값을 채워 주십시오"
        return ""


# 계정 정보를 적어 두는 파일. 이 폴더에 있고, .gitignore 로 커밋에서 빠진다.
SECRET_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mail.env")


def _load_secret_file() -> None:
    """mail.env 를 읽어 환경변수에 채운다. 이미 있는 값은 건드리지 않는다.

    켤 때마다 아이디·비밀번호를 묻지 않으려고 파일로 둔다. 환경변수를 직접
    설정한 경우에는 그쪽이 이긴다 — 발표장에서 잠깐 다른 계정으로 바꾸기 쉽다.
    """
    try:
        with open(SECRET_FILE, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and not os.getenv(key):
            os.environ[key] = value


def config() -> MailConfig:
    """환경변수를 읽는다. 기본값은 네이버 SMTP 다 (샘플 주소가 네이버라서)."""
    _load_secret_file()
    user = os.getenv("HR_SMTP_USER", "").strip()
    port = int(os.getenv("HR_SMTP_PORT", "587"))
    host = os.getenv("HR_SMTP_HOST", "smtp.naver.com")

    # 네이버·구글은 로그인 아이디와 보내는 주소가 다르게 적힌다.
    # 아이디만 넣어도 보내는 주소가 만들어지도록 도메인을 붙여 준다.
    sender = os.getenv("HR_MAIL_FROM", "").strip() or user
    if sender and "@" not in sender:
        domain = {"smtp.naver.com": "naver.com",
                  "smtp.gmail.com": "gmail.com"}.get(host)
        if domain:
            sender = f"{sender}@{domain}"

    return MailConfig(
        host=host,
        port=port,
        user=user,
        # 구글 앱 비밀번호는 'abcd efgh ijkl mnop' 처럼 4자리씩 띄어서 보여 준다.
        # 보이는 대로 붙여넣는 것이 자연스러운데, 공백이 섞이면 인증이 막힌다.
        password=re.sub(r"\s+", "", os.getenv("HR_SMTP_PASS", "")),
        sender=sender,
        sender_name=os.getenv("HR_MAIL_FROM_NAME", "HRD 피드백"),
        use_ssl=port == 465,
        enabled=_flag("HR_MAIL_ENABLED", "0"),
    )


def valid(address: Optional[str]) -> bool:
    return bool(address and EMAIL_RE.match(address.strip()))


_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def format_date(raw) -> str:
    """'2026-05-19' → 'Tue May 19'. 컴퓨터 지역 설정에 좌우되지 않게 직접 만든다."""
    text = str(raw or "").strip()
    if not text:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            d = _dt.datetime.strptime(text[:len(fmt) + 4], fmt)
        except ValueError:
            continue
        return f"{_DAYS[d.weekday()]} {_MONTHS[d.month - 1]} {d.day:02d}"
    return text                       # 못 읽으면 적힌 그대로 둔다


def report_body(name: str, program: str, round_: str = "",
                date: str = "", rater: str = "", team: str = "") -> str:
    """리포트 안내 메일 본문. 값이 없는 줄은 넣지 않는다."""
    title = " ".join(x for x in (program, round_) if x) or "개인"
    lines = [f"{name} 님,", "", f"{title} 개인 리포트를 첨부합니다."]

    meta = []
    if date:
        meta.append(f"기준일: {format_date(date)}")
    if rater:
        meta.append(f"평가자: {rater}")
    if meta:
        lines += [""] + meta

    lines += [
        "",
        "본 리포트는 수신자 본인에게만 발송되며, "
        "타 참가자의 평가 내용은 포함되어 있지 않습니다.",
        "문의 사항은 본 메일로 회신 바랍니다.",
        "",
        team or os.getenv("HR_MAIL_TEAM", "교육운영팀"),
    ]
    return "\n".join(lines)


def build_message(cfg: MailConfig, to: str, subject: str, body: str,
                  attachment: Optional[tuple] = None) -> EmailMessage:
    """본문은 짧은 안내글, 리포트는 HTML 파일로 붙인다.

    리포트를 본문에 통째로 넣으면 메일 앱마다 표가 깨지고, 받는 사람이
    나중에 다시 열어 보기도 어렵다. 파일로 주면 그대로 저장되고
    브라우저에서 열어 인쇄(PDF)까지 된다.
    """
    msg = EmailMessage()
    msg["From"] = formataddr((cfg.sender_name, cfg.sender or cfg.user))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment:
        filename, data = attachment
        msg.add_attachment(data, maintype="text", subtype="html",
                           filename=filename)
    return msg


def send(to: str, subject: str, body: str,
         attachment: Optional[tuple] = None,
         cfg: Optional[MailConfig] = None) -> dict:
    """한 통 보낸다. 보내지 못하는 상태면 미리보기 결과를 돌려준다.

    예외를 밖으로 던지지 않는다 — 한 통이 실패해도 나머지 발송이 멈추면 안 된다.
    """
    cfg = cfg or config()
    attached = attachment[0] if attachment else None
    size = len(attachment[1]) if attachment else 0

    if not valid(to):
        return {"to": to, "sent": False, "reason": "주소 형식이 아닙니다"}
    if not cfg.ready:
        return {"to": to, "sent": False, "dry_run": True,
                "reason": cfg.why_not(), "subject": subject,
                "attachment": attached, "bytes": size}

    msg = build_message(cfg, to, subject, body, attachment)
    try:
        if cfg.use_ssl:
            with smtplib.SMTP_SSL(cfg.host, cfg.port,
                                  context=ssl.create_default_context()) as smtp:
                smtp.login(cfg.user, cfg.password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(cfg.host, cfg.port, timeout=20) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(cfg.user, cfg.password)
                smtp.send_message(msg)
    except Exception as exc:                      # noqa: BLE001
        return {"to": to, "sent": False,
                "reason": f"{type(exc).__name__}: {exc}"}

    return {"to": to, "sent": True, "subject": subject}


def status() -> dict:
    cfg = config()
    return {"enabled": cfg.enabled, "ready": cfg.ready,
            "host": cfg.host, "port": cfg.port,
            "from": cfg.sender or None,
            "note": cfg.why_not() or "발송 준비됨"}
