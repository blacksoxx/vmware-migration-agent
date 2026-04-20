FROM python:3.12-slim

ARG TARGETARCH=amd64
ARG TERRAFORM_VERSION=1.8.5
ARG TFLINT_VERSION=0.53.0
ARG OPA_VERSION=0.68.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /tmp/terraform.zip \
    "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_${TARGETARCH}.zip" \
    && unzip /tmp/terraform.zip -d /usr/local/bin \
    && rm -f /tmp/terraform.zip

RUN curl -fsSL -o /tmp/tflint.zip \
    "https://github.com/terraform-linters/tflint/releases/download/v${TFLINT_VERSION}/tflint_linux_${TARGETARCH}.zip" \
    && unzip /tmp/tflint.zip -d /usr/local/bin \
    && chmod +x /usr/local/bin/tflint \
    && rm -f /tmp/tflint.zip

RUN curl -fsSL -o /usr/local/bin/opa \
    "https://openpolicyagent.org/downloads/v${OPA_VERSION}/opa_linux_${TARGETARCH}_static" \
    && chmod +x /usr/local/bin/opa

RUN terraform version \
    && tflint --version \
    && opa version

WORKDIR /app

COPY . /app

RUN pip install --upgrade pip \
    && pip install .

RUN pip install uv

RUN docker pull hashicorp/terraform-mcp-server:latest || true

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

CMD ["python", "-m", "cli.main"]
