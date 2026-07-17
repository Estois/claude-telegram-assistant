FROM python:3.11-slim

# curl/ca-certificates: fetch the Claude Code CLI installer.
# git: available to Claude Code / skills that shell out to it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# Native Claude Code CLI install (no Node/npm needed) — lands in ~/.local/bin.
RUN curl -fsSL https://claude.ai/install.sh | bash
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

CMD ["python", "bot.py"]
