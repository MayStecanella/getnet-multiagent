"""Knowledge Agent: query rewriting + RAG sobre getnet.net e Get Ajuda."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app import prompts
from app.config import settings
from app.llm import get_llm
from app.rag.retriever import Chunk, get_retriever
from app.state import AgentOutput, GraphState


class Queries(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=3)


def format_chunks(chunks: list[Chunk]) -> str:
    return "\n\n".join(f'<trecho url="{c.url}" titulo="{c.title}">\n{c.text}\n</trecho>' for c in chunks)


def knowledge_node(state: GraphState) -> GraphState:
    llm = get_llm()
    question = state["masked_message"]
    queries = llm.structured(prompts.QUERY_REWRITE, question, Queries).queries
    chunks = get_retriever().search([question, *queries], k=settings.top_k)

    if not chunks:
        content = (
            "Não encontrei essa informação na base de conhecimento. "
            "Você pode consultar a central Get Ajuda: https://site.getnet.com.br/get-ajuda/"
        )
        return {"outputs": [AgentOutput(agent="knowledge", content=content)]}

    answer = llm.text(prompts.KNOWLEDGE, f"Pergunta: {question}\n\n{format_chunks(chunks)}")
    sources = list(dict.fromkeys(c.url for c in chunks if c.url))
    return {"outputs": [AgentOutput(agent="knowledge", content=answer, sources=sources)]}
