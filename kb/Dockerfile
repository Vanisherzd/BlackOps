FROM python:3.12-slim

ARG APP_VERSION=0.3.0
WORKDIR /opt/blackout-ops

COPY dist/blackout_ops_kb-${APP_VERSION}-py3-none-any.whl /tmp/blackout_ops_kb.whl
RUN python3 -m pip install --no-cache-dir --no-deps /tmp/blackout_ops_kb.whl \
    && rm /tmp/blackout_ops_kb.whl \
    && useradd --create-home --uid 10001 blackout

USER blackout
ENTRYPOINT ["python3", "-m", "blackout_ops_kb"]
CMD ["doctor"]
