#!/bin/sh
# Sobe a API: prepara a base mock, garante o índice RAG e inicia o servidor.
# Porta: $PORT (o Render define), ou 8000 localmente.
set -e

python -m app.data.mock_db

if [ "${OFFLINE_MODE:-false}" = "true" ]; then
  echo "Modo off-line: LLM, RAG e busca web simulados (sem chave, sem internet)."
elif [ "${SKIP_INGEST:-false}" != "true" ]; then
  python -m app.rag.ingest   # só ingere se o índice ainda não existir
elif [ -n "${CHROMA_DIR:-}" ] && [ ! -d "${CHROMA_DIR}" ]; then
  echo "AVISO: índice RAG não encontrado em ${CHROMA_DIR}; o Knowledge vai responder sem base."
fi

if [ "${OFFLINE_MODE:-false}" != "true" ] && [ -z "${TAVILY_API_KEY:-}" ]; then
  echo "AVISO: TAVILY_API_KEY vazia: perguntas atuais (placar, clima, câmbio) ficam sem resposta."
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips="*"
