#!/bin/sh
# Prepara dados na primeira subida (base mock e índice RAG) e executa o comando recebido.
set -e

if [ "$1" = "uvicorn" ]; then
  python -m app.data.mock_db
  if [ "${SKIP_INGEST:-false}" != "true" ]; then
    python -m app.rag.ingest   # só ingere se o índice ainda não existir
  fi
fi

exec "$@"
