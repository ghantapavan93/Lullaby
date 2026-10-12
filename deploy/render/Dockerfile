# One container for Render's free web service: gateway, one workspace, its worker.
# Build context: the repository root.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY deploy/vm/requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt
COPY proof-layer/app /srv/proof-layer/app
COPY proof-layer/migrations /srv/proof-layer/migrations
COPY proof-layer/alembic.ini /srv/proof-layer/alembic.ini
COPY proof-layer/static /srv/proof-layer/static
# Certification compares the test report's code digest with the running code
# (app, migrations, static, tests): ship both, so the live Growth Lab can say
# whether the report describes this exact code.
COPY proof-layer/tests /srv/proof-layer/tests
COPY artifacts/test-results/final-backend.xml /srv/artifacts/test-results/final-backend.xml
COPY deploy/gateway/gateway.py deploy/gateway/headers.py /srv/deploy/gateway/
COPY deploy/landing /srv/deploy/landing
COPY deploy/render/run.py /srv/deploy/render/run.py
COPY docs/architecture /srv/docs/architecture
RUN useradd --system --home /srv app && chown -R app /srv
USER app
EXPOSE 10000
CMD ["python", "/srv/deploy/render/run.py"]
