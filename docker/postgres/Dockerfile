# Postgres 17 + pgvector (imagem oficial do pgvector) + pg_search (BM25 do ParadeDB).
#
# Estende a mesma imagem usada antes (pgvector/pgvector:pg17), então o volume de dados
# existente continua compatível (mesmo PGDATA, mesmo uid, mesma versão do pgvector).
# Só acrescenta a extensão pg_search, instalada do pacote .deb oficial com hash conferido.
FROM pgvector/pgvector:pg17

ARG PG_SEARCH_VERSION=0.26.5
ARG TARGETARCH=amd64
ARG PG_SEARCH_SHA256_amd64=ee7b35b95d7921916b23dadb18ad5af1b86497762b88c433cf908c4b2050f143
ARG PG_SEARCH_SHA256_arm64=9873201997a4ad4ed02cd8f7e58894227071faa244cf253b5a8a283804d8035b

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl ca-certificates; \
    deb="postgresql-17-pg-search_${PG_SEARCH_VERSION}-1PARADEDB-bookworm_${TARGETARCH}.deb"; \
    curl -fsSL -o "/tmp/${deb}" "https://github.com/paradedb/paradedb/releases/download/v${PG_SEARCH_VERSION}/${deb}"; \
    case "${TARGETARCH}" in \
      amd64) expected="${PG_SEARCH_SHA256_amd64}" ;; \
      arm64) expected="${PG_SEARCH_SHA256_arm64}" ;; \
      *) echo "arquitetura não suportada: ${TARGETARCH}"; exit 1 ;; \
    esac; \
    echo "${expected}  /tmp/${deb}" | sha256sum -c -; \
    apt-get install -y --no-install-recommends "/tmp/${deb}"; \
    rm -f "/tmp/${deb}"; \
    apt-get purge -y --auto-remove curl; \
    rm -rf /var/lib/apt/lists/*

# pg_search precisa ser carregada na inicialização do servidor.
CMD ["postgres", "-c", "shared_preload_libraries=pg_search"]
