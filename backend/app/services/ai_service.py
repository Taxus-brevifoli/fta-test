"""
AI 服务：基于请求内 RAG 检索结果生成故障树结构。
"""

import json
import re
import uuid
from typing import Any

from openai import OpenAI

from app.core.config import settings
from app.schemas.fta import (
    FtaAttr,
    FtaData,
    FtaGenerateResponse,
    FtaLink,
    FtaNode,
)
from app.schemas.knowledge import ParsedDocument, RetrievalOptions
from app.services import rag_service

_SYSTEM_PROMPT = """\
你是一名工业设备故障树分析（FTA）专家。
你的任务是：根据用户提供的顶事件、多轮 RAG 检索证据和附加要求，生成一棵完整、符合 FTA 规范的故障树。

输出要求（必须严格遵守）：
1. 只输出一个合法的 JSON 对象，不要输出任何额外文字、代码块标记或注释。
2. JSON 结构如下：
{
  "nodes": [
    {
      "id": "<唯一字符串ID>",
      "name": "<事件名称>",
      "type": "<1|2|3>",
      "gate": "<1|2>"
    }
  ],
  "links": [
    {
      "sourceId": "<父节点ID>",
      "targetId": "<子节点ID>"
    }
  ],
  "source_summary": "<简要说明你依据了哪些证据片段>",
  "evidence_summary": "<总结最关键的故障证据线索>"
}
3. type 字段含义：1=顶事件（只能有一个），2=中间事件，3=底事件（叶子节点，无子节点）。
4. gate 字段含义：1=AND门，2=OR门；底事件（type=3）的 gate 值填 2 即可，不会实际使用。
5. 故障树必须是有向无环树（DAG），顶事件为根，底事件为叶子。
6. 节点数量建议在 10～30 个之间，覆盖主要故障路径即可。
7. 优先使用证据中明确提到的直接原因、次级原因和失效链，不要臆造与证据无关的节点。
"""

_USER_PROMPT_TEMPLATE = """\
【顶事件】
{top_event}

【检索证据摘要】
{evidence_summary}

【多轮检索证据】
{rag_context}

{extra_section}请生成符合要求的故障树 JSON。
"""


def generate_fta_response(
    top_event: str,
    documents: list[ParsedDocument],
    doc_text: str = "",
    extra_prompt: str = "",
    retrieval_options: RetrievalOptions | None = None,
) -> FtaGenerateResponse:
    docs = rag_service.build_documents_input(documents, doc_text)
    retrieval_result = rag_service.retrieve_evidence(
        top_event=top_event,
        documents=docs,
        retrieval_options=retrieval_options,
    )

    raw = _call_llm(
        top_event=top_event,
        rag_context=retrieval_result.prompt_context,
        evidence_summary=retrieval_result.evidence_summary,
        extra_prompt=extra_prompt,
    )
    data = _extract_json(raw)
    nodes, links = _parse_llm_output(data)
    _assign_positions(nodes, links)

    return FtaGenerateResponse(
        fta_data=FtaData(
            attr=FtaAttr(),
            nodeList=nodes,
            linkList=links,
        ),
        source_summary=str(data.get("source_summary", "")).strip(),
        evidence_summary=str(data.get("evidence_summary", "")).strip()
        or retrieval_result.evidence_summary,
        evidence_items=retrieval_result.evidence_items,
        retrieval_trace=retrieval_result.retrieval_trace,
    )


def _call_llm(
    top_event: str,
    rag_context: str,
    evidence_summary: str,
    extra_prompt: str,
) -> str:
    client = OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )

    max_context_chars = 18000
    if len(rag_context) > max_context_chars:
        rag_context = rag_context[:max_context_chars] + "\n...(证据内容已截断)"

    extra_section = f"【附加要求】\n{extra_prompt}\n\n" if extra_prompt.strip() else ""
    user_content = _USER_PROMPT_TEMPLATE.format(
        top_event=top_event,
        rag_context=rag_context or "无",
        evidence_summary=evidence_summary or "无",
        extra_section=extra_section,
    )

    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0.2,
    )
    return response.choices[0].message.content or ""


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()
    return json.loads(cleaned)


def _parse_llm_output(data: dict[str, Any]) -> tuple[list[FtaNode], list[FtaLink]]:
    nodes = [
        FtaNode(
            id=str(node.get("id", uuid.uuid4())),
            name=str(node.get("name", "未命名节点")),
            type=str(node.get("type", "2")),
            gate=str(node.get("gate", "2")),
        )
        for node in data.get("nodes", [])
    ]

    links = [
        FtaLink(
            sourceId=str(link["sourceId"]),
            targetId=str(link["targetId"]),
        )
        for link in data.get("links", [])
        if "sourceId" in link and "targetId" in link
    ]

    return nodes, links


def _assign_positions(nodes: list[FtaNode], links: list[FtaLink]) -> None:
    if not nodes:
        return

    child_map: dict[str, list[str]] = {node.id: [] for node in nodes}
    has_parent: set[str] = set()
    for link in links:
        if link.sourceId in child_map:
            child_map[link.sourceId].append(link.targetId)
        has_parent.add(link.targetId)

    roots = [node.id for node in nodes if node.id not in has_parent] or [nodes[0].id]
    node_map = {node.id: node for node in nodes}

    layer = roots
    visited: set[str] = set(roots)
    layers: list[list[str]] = []

    while layer:
        layers.append(layer)
        next_layer: list[str] = []
        for node_id in layer:
            for child in child_map.get(node_id, []):
                if child not in visited:
                    visited.add(child)
                    next_layer.append(child)
        layer = next_layer

    y_gap = 160
    x_gap = 200

    for depth, layer_ids in enumerate(layers):
        total_width = (len(layer_ids) - 1) * x_gap
        start_x = -total_width / 2
        for index, node_id in enumerate(layer_ids):
            if node_id in node_map:
                node_map[node_id].x = round(start_x + index * x_gap)
                node_map[node_id].y = round(depth * y_gap)

    isolated = [node for node in nodes if node.id not in visited]
    if isolated:
        max_x = max((node.x for node in nodes if node.id in visited), default=0)
        for index, node in enumerate(isolated):
            node.x = round(max_x + x_gap * (index + 1))
            node.y = 0

