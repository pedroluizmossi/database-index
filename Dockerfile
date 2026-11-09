# Imagem da aplicação (API REST + MCP + interface web). Ver o serviço `app` no docker-compose.yml.

# --- interface web (frontend/) ---
FROM node:22-bookworm-slim AS web
WORKDIR /web
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# --- aplicação Python ---
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# git: clones dos repositórios; ripgrep: busca exata (infra/grep.py)
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends git ripgrep ca-certificates; \
    rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
# Dependências primeiro (camada em cache enquanto o uv.lock não muda)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY README.md ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev
# app.py procura a interface em <raiz do projeto>/frontend/dist
COPY --from=web /web/dist ./frontend/dist

# Os clones e o estado do CocoIndex são montados pelo compose (CODE_INDEX_HOME); o git reclama de
# repositórios de outro dono sem isto.
RUN git config --system --add safe.directory '*'

EXPOSE 8765
CMD ["code-index", "serve", "--host", "0.0.0.0"]
