# CS2 price parser. A pass is a batch job: the container runs one CLI
# invocation and exits, so there is no server process and no EXPOSE.
FROM python:3.12-slim-bookworm

WORKDIR /app

# psycopg2-binary ships manylinux wheels, so no compiler is needed in the image.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY alembic/ ./alembic/
COPY src/ ./src/

ENV PYTHONPATH=/app/src
# Without this the per-request log lines sit in a buffer for minutes, and a
# pass that dies on a 429 loses the tail — which is exactly the part that says
# where it died (docs/steam-parser-plan.md step 5).
ENV PYTHONUNBUFFERED=1

# Args come from `docker compose run --rm parser <args>`; `--entrypoint alembic`
# overrides this for migrations.
ENTRYPOINT ["python", "-m", "price_compare"]
CMD ["--help"]
