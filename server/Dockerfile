# worship-tools v2. 빌드 컨텍스트는 저장소 루트(deploy/compose.yaml 참고).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STATIC_ROOT=/srv/static \
    DB_PATH=/data/worship.db

WORKDIR /srv/server
COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/app ./app
COPY web /srv/static/web
COPY vendor /srv/static/vendor
COPY fonts /srv/static/fonts

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

# 작업 실행기와 SQLite 때문에 워커는 1개로 둔다.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips=*", "--no-access-log"]
