"""프로젝트 폴더의 `*.env` 파일을 읽어 환경변수에 채운다.

API 키와 메일 비밀번호를 서버 켤 때마다 손으로 넣게 하고 싶지 않다.
그렇다고 코드에 적을 수도 없다. 그래서 폴더 안의 `*.env` 파일에 한 번 적어
두고, 그 파일들은 `.gitignore` 로 커밋에서 빠지게 한다.

    api.env    ANTHROPIC_API_KEY=sk-ant-...
    mail.env   HR_SMTP_PASS=...

이미 설정된 환경변수는 건드리지 않는다 — 잠깐 다른 키로 바꿔 볼 때
PowerShell 에서 `$env:ANTHROPIC_API_KEY = "..."` 한 줄이면 되게 하기 위해서다.

**빈 값도 설정된 것으로 본다.** 예전에는 빈 값을 '안 넣은 것'으로 보고 파일에서
채웠는데, 그 바람에 `ANTHROPIC_API_KEY=""` 로 꺼 놓고 목 모드로 돌린다고 믿었던
시험들이 실제로는 API 를 부르고 있었다. 끄겠다는 뜻으로 빈 값을 넣는 것이니
그 뜻을 그대로 따른다.
"""
from __future__ import annotations

import glob
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
_loaded = False


def load(force: bool = False) -> None:
    """`*.env` 를 읽어 os.environ 을 채운다. 여러 번 불러도 한 번만 읽는다."""
    global _loaded
    if _loaded and not force:
        return
    _loaded = True

    for path in sorted(glob.glob(os.path.join(ROOT, "*.env"))):
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value
