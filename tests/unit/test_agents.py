from app.agents.composer import composer_node
from app.agents.knowledge import knowledge_node
from app.agents.router import normalize_routes, router_node
from app.agents.self_service import self_service_node
from app.agents.support import support_node
from app.config import settings
from app.state import AgentOutput
from app.tools.catalog import load_catalog
from tests.fakes import FakeRetriever


def _state(msg):
    return {"message": msg, "masked_message": msg, "user_id": "cliente1988"}


# --- Router -------------------------------------------------------------
def test_router_normalizes_routes():
    assert normalize_routes(["knowledge", "knowledge"]) == ["knowledge"]
    assert normalize_routes(["support", "self_service"]) == ["support"]
    assert normalize_routes([]) == ["support"]


def test_router_combines_knowledge_and_self_service():
    out = router_node(_state("Quero antecipar, como funciona a antecipação?"))
    assert out["routes"] == ["knowledge", "self_service"]
    assert out["theme"] == "antecipacao"


# --- Knowledge ----------------------------------------------------------
def test_knowledge_cites_sources(fake_retriever):
    out = knowledge_node(_state("Preciso de conta bancária para receber via Pix?"))["outputs"][0]
    assert out.agent == "knowledge"
    assert out.sources and all(s.startswith("https://site.getnet.com.br") for s in out.sources)
    assert len(fake_retriever.queries[0]) >= 2  # pergunta original + reescritas


def test_knowledge_admits_when_nothing_is_found():
    from app.rag import retriever
    retriever.set_retriever(FakeRetriever(chunks=[]))
    out = knowledge_node(_state("Pergunta sem resposta"))["outputs"][0]
    assert "Não encontrei" in out.content and "get-ajuda" in out.content


# --- Autosserviço -------------------------------------------------------
def test_catalog_has_the_13_requests_without_duplicates():
    catalog = load_catalog()
    assert len(catalog) == 13
    assert len({c["name"] for c in catalog.values()}) == 13


def test_self_service_points_to_digital_channels():
    out = self_service_node(_state("Preciso pedir bobinas"))["outputs"][0]
    assert "Solicitação de bobinas" in out.content
    assert settings.portal_url in out.content and "Sou cliente" in out.content
    assert out.tools_used == ["catalog:bobinas"]


def test_self_service_without_match_still_shows_channels():
    out = self_service_node(_state("quero fazer uma coisa"))["outputs"][0]
    assert settings.portal_url in out.content


# --- Support ------------------------------------------------------------
def test_support_uses_settlement_tool():
    out = support_node(_state("Quando cai o dinheiro das vendas de ontem?"))["outputs"][0]
    assert out.tools_used == ["get_settlements"]
    assert "R$" in out.content and "plano" in out.content


def test_support_uses_transactions_and_terminal_for_decline():
    out = support_node(_state("Minha máquina está mostrando erro de transação recusada (decline)"))["outputs"][0]
    assert set(out.tools_used) == {"get_transactions", "get_terminal_status"}


def test_support_survives_rag_failure():
    class Broken:
        def search(self, q, k):
            raise RuntimeError("chroma fora")
    from app.rag import retriever
    retriever.set_retriever(Broken())
    out = support_node(_state("Minha máquina não conecta na internet"))["outputs"][0]
    assert out.tools_used == ["get_terminal_status"]


# --- Compositor ---------------------------------------------------------
def test_composer_greets_once_with_two_agents():
    outputs = [AgentOutput(agent="knowledge", content="explicação"),
               AgentOutput(agent="self_service", content="canal")]
    resp = composer_node({"masked_message": "x", "outputs": outputs})["response"]
    assert resp.count("Olá") == 1 and "explicação" in resp and "canal" in resp
