# cestaWEB — imagem única, um processo.
#
# Sem Celery, sem worker, sem banco: este app recebe uma planilha, lê e repassa
# ao OCTO por HTTP. Se algum dia esta imagem precisar de um segundo processo,
# vale reler a fronteira no CONTRATO.md antes.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY cli.py ./
COPY CONTRATO.md ./

# Usuário sem privilégio: o processo abre arquivo enviado por terceiro, que é
# exatamente o tipo de entrada que não merece rodar como root.
RUN useradd --create-home --shell /usr/sbin/nologin cesta \
    && chown -R cesta:cesta /app
USER cesta

# O Railway injeta PORT. O default cobre `docker run` local.
ENV PORT=8080
EXPOSE 8080

# `sh -c` porque $PORT precisa ser expandido em runtime, não no build.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
