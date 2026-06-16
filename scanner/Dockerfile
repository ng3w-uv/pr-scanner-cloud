FROM semgrep/semgrep:latest

USER root

RUN apk add --no-cache git libpq \
    && python3 -m pip install --no-cache-dir --break-system-packages boto3 "psycopg2-binary>=2.9"

WORKDIR /app
COPY scan.py /app/scan.py

ENTRYPOINT ["python3", "/app/scan.py"]
