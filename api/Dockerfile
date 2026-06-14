FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv

# Run as a non-root user.
RUN useradd -m -u 10001 app

# NOTE: build context is the REPO ROOT (see docker-compose api service), so paths are
# repo-relative. The routers import the judge core + load problems at startup/first use,
# so judge/ and problems/ MUST be in the image (grading._JUDGE resolves to /judge,
# registry.PROBLEMS_DIR to /problems).
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/app ./app
COPY judge /judge
COPY problems /problems

USER app
EXPOSE 8000
# Bind the platform-provided $PORT when present (Render/Fly/etc.), else 8000 (compose).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
