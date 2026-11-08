import os
import tempfile

# DATA_DIR precisa existir antes de importar app.config
# Sempre um diretório temporário: os testes nunca tocam nos dados reais do volume
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="getnet-test-")
os.environ["TAVILY_API_KEY"] = ""
os.environ["OFFLINE_MODE"] = "true"   # garantia: nenhum teste chama LLM, RAG ou web de verdade
os.environ["ACCESS_CODE"] = ""

import pytest  # noqa: E402

from app import llm  # noqa: E402
from app.config import settings  # noqa: E402
from app.data.mock_db import seed  # noqa: E402
from app.rag import retriever  # noqa: E402
from tests.fakes import FakeLLM, FakeRetriever  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_env():
    """Base mock e banco de atendimentos limpos em cada teste, com LLM e RAG simulados."""
    if settings.db_path.exists():
        settings.db_path.unlink()
    seed()
    fake_llm, fake_retriever = FakeLLM(), FakeRetriever()
    llm.set_llms(fake_llm)
    retriever.set_retriever(fake_retriever)
    from app.web.handlers import limiter
    limiter.reset()
    yield fake_llm, fake_retriever


@pytest.fixture
def fake_llm(fresh_env):
    return fresh_env[0]


@pytest.fixture
def fake_retriever(fresh_env):
    return fresh_env[1]
