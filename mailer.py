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

import gmail_api
import localenv

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
        """실제 발송에 필요한 것이 다 있는가.

        지메일 HTTPS 길이 열려 있으면 SMTP 계정은 없어도 된다. 보내는 주소는
        구글 계정이 정하므로, 우리가 알아야 할 것은 '보낼 수 있는가' 뿐이다.
        """
        if self.enabled and gmail_api.configured():
            return True
        return bool(self.enabled and self.host and self.user and self.password)

    def why_not(self) -> str:
        """왜 못 보내는지. **어디를 고쳐야 하는지까지** 적는다.

        내 컴퓨터에서 돌 때와 서버에 올렸을 때 고칠 자리가 다르다. 서버에는
        mail.env 가 없는데 "mail.env 를 고치라"고 하면 없는 파일을 찾게 된다.
        """
        where = _where()
        if not self.enabled:
            return f"{where} 의 HR_MAIL_ENABLED 가 꺼져 있습니다"
        if gmail_api.configured():
            return ""
        missing = [n for n, v in (("HR_SMTP_HOST", self.host),
                                  ("HR_SMTP_USER", self.user),
                                  ("HR_SMTP_PASS", self.password)) if not v]
        if missing:
            return f"{where} 에 {', '.join(missing)} 값을 채워 주십시오"
        return ""


def _where() -> str:
    """설정을 고칠 자리. 배포된 서버에는 mail.env 가 없다."""
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mail.env")
    return "mail.env" if os.path.exists(here) else "서버 환경변수(Environment)"


def config() -> MailConfig:
    """환경변수를 읽는다. 기본값은 네이버 SMTP 다 (샘플 주소가 네이버라서)."""
    localenv.load()
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


def check(cfg: Optional[MailConfig] = None) -> dict:
    """**보내지 않고** 접속·로그인만 해 본다.

    발송이 실패할 때 이유가 여럿이다 — 앱 비밀번호가 틀렸는가, 서버가 메일
    포트를 막았는가, 계정이 잠겼는가. 실제로 메일을 쏘아 보며 알아내면
    되돌릴 수 없는 메일이 나가고, 실패해도 왜인지는 그대로 모른다.
    여기서는 문 앞까지만 가 보고 돌아온다.
    """
    cfg = cfg or config()
    if not cfg.enabled:
        return {"ok": False, "step": "설정", "reason": cfg.why_not(),
                "hint": "값을 채우고 다시 확인하십시오"}
    if gmail_api.configured():
        return gmail_api.check()
    if not cfg.ready:
        return {"ok": False, "step": "설정", "reason": cfg.why_not(),
                "hint": "값을 채우고 다시 확인하십시오"}

    step = "연결"
    try:
        if cfg.use_ssl:
            smtp = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=20,
                                    context=ssl.create_default_context())
        else:
            smtp = smtplib.SMTP(cfg.host, cfg.port, timeout=20)
        with smtp:
            step = "보안 연결(STARTTLS)"
            if not cfg.use_ssl:
                smtp.starttls(context=ssl.create_default_context())
            step = "로그인"
            smtp.login(cfg.user, cfg.password)
        return {"ok": True, "step": "완료",
                "reason": f"{cfg.host}:{cfg.port} 에 {cfg.user} 로 로그인됐습니다"}
    except Exception as exc:                    # noqa: BLE001
        name = type(exc).__name__
        return {"ok": False, "step": step,
                "reason": f"{name}: {exc}",
                "hint": _hint(name, str(exc), cfg)}


def _hint(name: str, msg: str, cfg: MailConfig) -> str:
    """무엇을 고쳐야 하는지. 오류 이름만으로는 알 수 없다."""
    low = msg.lower()
    if "authentication" in name.lower() or "5.7.8" in msg or "not accepted" in low:
        return ("앱 비밀번호가 맞지 않습니다. 구글 계정 > 보안 > 2단계 인증을 "
                "켠 뒤 '앱 비밀번호'를 새로 발급해 HR_SMTP_PASS 에 넣으십시오. "
                "구글 로그인 비밀번호로는 SMTP 가 막힙니다.")
    if name in ("TimeoutError", "socket.timeout") or "timed out" in low:
        return (f"{cfg.host}:{cfg.port} 로 나가는 길이 막혀 있습니다. "
                f"무료 호스팅은 메일 포트를 막아 두는 경우가 많습니다 — "
                f"포트 465(SSL)로 바꿔 보시고, 그래도 안 되면 호스팅이 "
                f"SMTP 를 허용하는지 확인해야 합니다.")
    if name in ("ConnectionRefusedError", "SMTPConnectError", "OSError",
                "gaierror", "SMTPServerDisconnected"):
        return (f"{cfg.host}:{cfg.port} 에 닿지 못했습니다. 주소·포트를 "
                f"확인하시고, 호스팅이 바깥 메일 연결을 막는지 보십시오.")
    if "SMTPSenderRefused" in name:
        return "보내는 주소가 거부됐습니다. HR_SMTP_USER 가 실제 계정인지 확인하십시오."
    return "위 메시지를 그대로 알려 주시면 원인을 짚겠습니다."


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

    # 지메일 HTTPS 길이 열려 있으면 그쪽으로 간다. 호스팅이 SMTP 를 막아도
    # 이 길은 막히지 않는다. 메일 내용은 위에서 이미 다 만들었으므로
    # 운반 수단만 갈아탄다 — 두 군데서 조립하면 내용이 달라진다.
    if gmail_api.configured():
        ok, info = gmail_api.send_raw(msg.as_bytes())
        if ok:
            return {"to": to, "sent": True, "subject": subject,
                    "via": "gmail-api"}
        return {"to": to, "sent": False, "via": "gmail-api",
                "reason": f"Gmail API — {info}"}

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
    via = "gmail-api" if gmail_api.configured() else "smtp"
    return {"enabled": cfg.enabled, "ready": cfg.ready, "via": via,
            "host": "gmail.googleapis.com (HTTPS)" if via == "gmail-api" else cfg.host,
            "port": 443 if via == "gmail-api" else cfg.port,
            "from": cfg.sender or None,
            "note": cfg.why_not() or "발송 준비됨"}
