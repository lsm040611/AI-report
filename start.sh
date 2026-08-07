#!/usr/bin/env bash
# 리눅스·맥에서 서버를 띄운다. Windows 는 start.bat 을 쓰면 된다.
#
#   chmod +x start.sh      (처음 한 번만)
#   ./start.sh
set -e
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
command -v "$PY" >/dev/null || { echo "python3 이 없습니다. sudo apt install python3 python3-pip"; exit 1; }

# 처음 실행이면 필요한 패키지를 깔아 준다. 이미 있으면 넘어간다.
if ! "$PY" -c "import fastapi, openpyxl" 2>/dev/null; then
  echo "필요한 패키지를 설치합니다 (1~2분)..."
  "$PY" -m pip install -r requirements.txt
fi

PORT=${PORT:-8000}
echo
echo "  http://127.0.0.1:${PORT}"
echo "  끄려면 Ctrl+C"
echo
exec "$PY" -m uvicorn main:app --host 127.0.0.1 --port "$PORT"
