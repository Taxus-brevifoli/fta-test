"""
请求内 RAG 构建与检索服务。
"""

from __future__ import annotations

import json
import math
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

from openai import OpenAI

from app.core.config import settings
from app.schemas.knowledge import (
    DocumentSection,
    EvidenceItem,
    ParsedDocument,
    RetrievalOptions,
    RetrievalTraceItem,
)

try:
    from rank_bm25 import BM25Okapi
except ImportError:  # pragma: no cover - fallback for environments without the extra dependency
    BM25Okapi = None

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])\s*")


@dataclass
class Chunk:
    chunk_id: str
    text: str
    filename: str
    section_id: str
    section_order: int
    chunk_order: int
    title: str
    heading_path: list[str]
    page_start: int | None = None
    page_end: int | None = None
    paragraph_start: int | None = None
    paragraph_end: int | None = None
    prev_id: str | None = None
    next_id: str | None = None
    embedding: list[float] | None = None


@dataclass
class RetrievalCandidate:
    chunk: Chunk
    query: str = ""
    bm25_score: float = 0.0
    embedding_score: float = 0.0
    rerank_score: float = 0.0
    sources: set[str] = field(default_factory=set)

    @property
    def combined_score(self) -> float:
        return max(self.rerank_score, (self.bm25_score + self.embedding_score) / 2)


@dataclass
class RagRetrievalResult:
    evidence_items: list[EvidenceItem]
    retrieval_trace: list[RetrievalTraceItem]
    evidence_summary: str
    prompt_context: str


class _FallbackBM25:
    def __init__(self, corpus_tokens: list[list[str]]):
        self.corpus_tokens = corpus_tokens

    def get_scores(self, query_tokens: list[str]) -> list[float]:
        scores: list[float] = []
        query_set = set(query_tokens)
        for tokens in self.corpus_tokens:
            if not tokens:
                scores.append(0.0)
                continue
            token_set = set(tokens)
            overlap = len(query_set & token_set)
            scores.append(overlap / math.sqrt(len(token_set) + 1))
        return scores


def build_documents_input(documents: list[ParsedDocument], doc_text: str) -> list[ParsedDocument]:
    if documents:
        return documents
    cleaned_text = (doc_text or "").strip()
    if not cleaned_text:
        return []
    return [
        ParsedDocument(
            filename="manual-input.txt",
            filetype="txt",
            text=cleaned_text,
            char_count=len(cleaned_text),
            preview=cleaned_text[:200],
            sections=[
                DocumentSection(
                    id="manual-sec-1",
                    order=0,
                    title="手工输入文本",
                    heading_path=["手工输入文本"],
                    paragraph_start=1,
                    paragraph_end=1,
                    text=cleaned_text,
                )
            ],
        )
    ]


def retrieve_evidence(
    top_event: str,
    documents: list[ParsedDocument],
    retrieval_options: RetrievalOptions | None = None,
) -> RagRetrievalResult:
    options = retrieval_options or RetrievalOptions()
    chunks = _build_chunks(documents)
    if not chunks:
        return RagRetrievalResult(
            evidence_items=[],
            retrieval_trace=[],
            evidence_summary="未检索到可用证据。",
            prompt_context="",
        )

    corpus_tokens = [_tokenize(_chunk_index_text(chunk)) for chunk in chunks]
    bm25_model = BM25Okapi(corpus_tokens) if BM25Okapi else _FallbackBM25(corpus_tokens)
    _populate_chunk_embeddings(chunks)

    queue = deque([(top_event.strip(), 0)])
    seen_queries: set[str] = set()
    all_candidates: dict[str, RetrievalCandidate] = {}
    trace_items: list[RetrievalTraceItem] = []
    processed_queries = 0

    while queue and processed_queries < options.bfs_max_queries:
        query, depth = queue.popleft()
        normalized_query = _normalize_query(query)
        if not normalized_query or normalized_query in seen_queries:
            continue

        seen_queries.add(normalized_query)
        processed_queries += 1

        ranked_candidates = _retrieve_for_query(
            query=query,
            depth=depth,
            chunks=chunks,
            bm25_model=bm25_model,
            options=options,
        )

        for candidate in ranked_candidates:
            existing = all_candidates.get(candidate.chunk.chunk_id)
            if existing is None:
                all_candidates[candidate.chunk.chunk_id] = candidate
                continue
            existing.bm25_score = max(existing.bm25_score, candidate.bm25_score)
            existing.embedding_score = max(existing.embedding_score, candidate.embedding_score)
            existing.rerank_score = max(existing.rerank_score, candidate.rerank_score)
            existing.sources.update(candidate.sources)

        expanded_evidence = _expand_context(ranked_candidates, chunks, options.context_window)
        next_queries = []
        if depth < options.bfs_max_depth and expanded_evidence:
            next_queries = _extract_follow_up_queries(top_event, query, expanded_evidence)
            for next_query in next_queries:
                next_key = _normalize_query(next_query)
                if next_key and next_key not in seen_queries:
                    queue.append((next_query, depth + 1))

        trace_items.append(
            RetrievalTraceItem(
                query=query,
                depth=depth,
                evidence=expanded_evidence,
                next_queries=next_queries,
            )
        )

    final_ranked = sorted(
        all_candidates.values(),
        key=lambda item: item.combined_score,
        reverse=True,
    )[: options.final_evidence_k]
    final_evidence = _expand_context(final_ranked, chunks, options.context_window)
    prompt_context = _build_prompt_context(final_evidence)
    evidence_summary = _build_evidence_summary(final_evidence)

    return RagRetrievalResult(
        evidence_items=final_evidence,
        retrieval_trace=trace_items,
        evidence_summary=evidence_summary,
        prompt_context=prompt_context,
    )


def _build_chunks(documents: list[ParsedDocument]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for doc_index, document in enumerate(documents):
        for section in document.sections or _fallback_sections(document.text):
            section_chunks = _chunk_section(document.filename, section, doc_index)
            chunks.extend(section_chunks)

    for index, chunk in enumerate(chunks):
        chunk.prev_id = chunks[index - 1].chunk_id if index > 0 else None
        chunk.next_id = chunks[index + 1].chunk_id if index + 1 < len(chunks) else None

    return chunks


def _fallback_sections(text: str) -> list[DocumentSection]:
    if not text.strip():
        return []
    return [
        DocumentSection(
            id="fallback-sec-1",
            order=0,
            title="",
            heading_path=[],
            paragraph_start=1,
            paragraph_end=1,
            text=text,
        )
    ]


def _chunk_section(filename: str, section: DocumentSection, doc_index: int) -> list[Chunk]:
    text = section.text.strip()
    if not text:
        return []

    semantic_units = [unit for unit in re.split(r"\n\s*\n+", text) if unit.strip()]
    if not semantic_units:
        semantic_units = [text]

    expanded_units: list[str] = []
    for unit in semantic_units:
        normalized_unit = unit.strip()
        if len(normalized_unit) <= 1400:
            expanded_units.append(normalized_unit)
            continue
        expanded_units.extend(_split_long_unit(normalized_unit))

    chunks: list[Chunk] = []
    buffer: list[str] = []
    target_chars = 900
    min_chars = 260

    def flush() -> None:
        if not buffer:
            return
        chunk_text = "\n\n".join(buffer).strip()
        if chunk_text:
            chunk_order = len(chunks)
            chunks.append(
                Chunk(
                    chunk_id=f"d{doc_index}-s{section.order}-c{chunk_order}",
                    text=chunk_text,
                    filename=filename,
                    section_id=section.id,
                    section_order=section.order,
                    chunk_order=chunk_order,
                    title=section.title,
                    heading_path=list(section.heading_path),
                    page_start=section.page_start,
                    page_end=section.page_end,
                    paragraph_start=section.paragraph_start,
                    paragraph_end=section.paragraph_end,
                )
            )
        buffer.clear()

    for unit in expanded_units:
        candidate_text = "\n\n".join([*buffer, unit]).strip()
        if buffer and len(candidate_text) > target_chars and len("\n\n".join(buffer)) >= min_chars:
            flush()
        buffer.append(unit)

    flush()
    return chunks


def _split_long_unit(text: str) -> list[str]:
    sentences = [item.strip() for item in _SENTENCE_SPLIT_RE.split(text) if item.strip()]
    if len(sentences) <= 1:
        return [text]

    segments: list[str] = []
    buffer: list[str] = []
    for sentence in sentences:
        candidate = "".join([*buffer, sentence])
        if buffer and len(candidate) > 1000:
            segments.append("".join(buffer).strip())
            buffer = [sentence]
            continue
        buffer.append(sentence)

    if buffer:
        segments.append("".join(buffer).strip())
    return segments


def _retrieve_for_query(
    query: str,
    depth: int,
    chunks: list[Chunk],
    bm25_model,
    options: RetrievalOptions,
) -> list[RetrievalCandidate]:
    query_tokens = _tokenize(query)
    bm25_scores = bm25_model.get_scores(query_tokens)
    bm25_indices = _top_indices(bm25_scores, options.bm25_top_k)

    query_embedding = _embed_query(query)
    embedding_scores = [
        _cosine_similarity(query_embedding, chunk.embedding or [])
        for chunk in chunks
    ]
    embedding_indices = _top_indices(embedding_scores, options.embedding_top_k)

    candidates: dict[str, RetrievalCandidate] = {}
    for index in bm25_indices:
        chunk = chunks[index]
        candidate = candidates.setdefault(chunk.chunk_id, RetrievalCandidate(chunk=chunk, query=query))
        candidate.bm25_score = _normalize_score(bm25_scores[index], bm25_scores)
        candidate.sources.add("bm25")

    for index in embedding_indices:
        chunk = chunks[index]
        candidate = candidates.setdefault(chunk.chunk_id, RetrievalCandidate(chunk=chunk, query=query))
        candidate.embedding_score = _normalize_score(embedding_scores[index], embedding_scores)
        candidate.sources.add("embedding")

    candidate_list = list(candidates.values())
    reranked = _rerank_candidates(query, candidate_list, options.rerank_top_k, depth)
    rerank_map = {item["chunk_id"]: item["score"] for item in reranked}

    for candidate in candidate_list:
        candidate.rerank_score = rerank_map.get(
            candidate.chunk.chunk_id,
            (candidate.bm25_score + candidate.embedding_score) / 2,
        )

    return sorted(candidate_list, key=lambda item: item.combined_score, reverse=True)[: options.rerank_top_k]


def _expand_context(
    candidates: Iterable[RetrievalCandidate],
    chunks: list[Chunk],
    context_window: int,
) -> list[EvidenceItem]:
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
    section_map: dict[tuple[str, int], list[Chunk]] = {}
    for chunk in chunks:
        section_map.setdefault((chunk.filename, chunk.section_order), []).append(chunk)

    evidence_items: list[EvidenceItem] = []
    seen: set[str] = set()

    for candidate in candidates:
        section_chunks = section_map.get((candidate.chunk.filename, candidate.chunk.section_order), [])
        if not section_chunks:
            section_chunks = [candidate.chunk]
        section_chunks = sorted(section_chunks, key=lambda item: item.chunk_order)
        start = max(0, candidate.chunk.chunk_order - context_window)
        end = min(len(section_chunks), candidate.chunk.chunk_order + context_window + 1)
        expanded = section_chunks[start:end]

        text = "\n\n".join(_decorate_chunk_text(chunk) for chunk in expanded).strip()
        evidence_key = f"{candidate.chunk.chunk_id}:{start}:{end}"
        if evidence_key in seen:
            continue
        seen.add(evidence_key)

        first = expanded[0]
        last = expanded[-1]
        evidence_items.append(
            EvidenceItem(
                chunk_id=candidate.chunk.chunk_id,
                query=candidate.query,
                filename=candidate.chunk.filename,
                score=round(candidate.combined_score, 4),
                title=candidate.chunk.title,
                heading_path=candidate.chunk.heading_path,
                text=text,
                page_start=first.page_start,
                page_end=last.page_end,
            )
        )
    return evidence_items


def _decorate_chunk_text(chunk: Chunk) -> str:
    prefix_parts = [f"来源文件：{chunk.filename}"]
    if chunk.title:
        prefix_parts.append(f"标题：{chunk.title}")
    if chunk.page_start:
        page_label = (
            f"{chunk.page_start}"
            if chunk.page_end in (None, chunk.page_start)
            else f"{chunk.page_start}-{chunk.page_end}"
        )
        prefix_parts.append(f"页码：{page_label}")
    return f"[{' | '.join(prefix_parts)}]\n{chunk.text}"


def _build_prompt_context(evidence_items: list[EvidenceItem]) -> str:
    blocks = []
    for index, item in enumerate(evidence_items, start=1):
        heading = " > ".join(item.heading_path) if item.heading_path else (item.title or "未命名章节")
        blocks.append(
            f"证据 {index}\n"
            f"- 文件：{item.filename}\n"
            f"- 标题：{heading}\n"
            f"- 页码：{item.page_start or '-'}{f'~{item.page_end}' if item.page_end and item.page_end != item.page_start else ''}\n"
            f"- 内容：\n{item.text}"
        )
    return "\n\n".join(blocks)


def _build_evidence_summary(evidence_items: list[EvidenceItem]) -> str:
    if not evidence_items:
        return "未检索到有效证据。"
    lines = []
    for item in evidence_items:
        heading = " > ".join(item.heading_path) if item.heading_path else (item.title or "未命名章节")
        lines.append(f"{item.filename} / {heading} / score={item.score:.3f}")
    return "\n".join(lines)


def _extract_follow_up_queries(top_event: str, current_query: str, evidence_items: list[EvidenceItem]) -> list[str]:
    preview = "\n\n".join(item.text[:500] for item in evidence_items[:3])
    if not preview.strip():
        return []

    client = _get_client()
    model = settings.llm_rerank_model or settings.llm_model
    prompt = (
        "你是一名工业故障分析助手。"
        "请基于顶事件、当前查询和证据，提取 2 到 4 个下一轮 BFS 检索查询。"
        "这些查询应是更具体的直接原因或次级原因，避免复述原句。"
        "只输出 JSON：{\"queries\": [\"...\", \"...\"]}。"
    )
    try:
        response = client.chat.completions.create(
            model=model,
            temperature=0.1,
            messages=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": (
                        f"顶事件：{top_event}\n"
                        f"当前查询：{current_query}\n"
                        f"证据：\n{preview}"
                    ),
                },
            ],
        )
        text = response.choices[0].message.content or ""
        data = _extract_json(text)
        queries = [str(item).strip() for item in data.get("queries", []) if str(item).strip()]
        return queries[:4]
    except Exception:
        return _fallback_follow_up_queries(top_event, current_query, evidence_items)


def _fallback_follow_up_queries(
    top_event: str,
    current_query: str,
    evidence_items: list[EvidenceItem],
) -> list[str]:
    candidates: list[str] = []
    for item in evidence_items[:4]:
        heading = " > ".join(item.heading_path) if item.heading_path else item.title
        if heading and heading not in candidates and heading != current_query:
            candidates.append(f"{top_event} 的原因：{heading}")
        first_sentence = _split_long_unit(item.text)[0] if item.text else ""
        if first_sentence and first_sentence not in candidates and first_sentence != current_query:
            candidates.append(first_sentence[:80])
        if len(candidates) >= 4:
            break
    return candidates[:4]


def _rerank_candidates(
    query: str,
    candidates: list[RetrievalCandidate],
    rerank_top_k: int,
    depth: int,
) -> list[dict]:
    if not candidates:
        return []

    sorted_candidates = sorted(
        candidates,
        key=lambda item: (item.bm25_score + item.embedding_score),
        reverse=True,
    )[: max(rerank_top_k, 4)]

    client = _get_client()
    model = settings.llm_rerank_model or settings.llm_model
    candidate_payload = [
        {
            "chunk_id": item.chunk.chunk_id,
            "filename": item.chunk.filename,
            "title": item.chunk.title,
            "heading_path": item.chunk.heading_path,
            "text": item.chunk.text[:800],
        }
        for item in sorted_candidates
    ]
    try:
        response = client.chat.completions.create(
            model=model,
            temperature=0.0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一名检索重排器。"
                        "请根据查询与候选文本的相关性打分，分数范围 0 到 1。"
                        "只输出 JSON：{\"items\": [{\"chunk_id\": \"...\", \"score\": 0.92, \"reason\": \"...\"}]}。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"检索深度：{depth}\n"
                        f"查询：{query}\n"
                        f"候选：{json.dumps(candidate_payload, ensure_ascii=False)}"
                    ),
                },
            ],
        )
        text = response.choices[0].message.content or ""
        data = _extract_json(text)
        items = data.get("items", [])
        parsed = []
        for item in items:
            chunk_id = str(item.get("chunk_id", "")).strip()
            if not chunk_id:
                continue
            try:
                score = float(item.get("score", 0))
            except (TypeError, ValueError):
                score = 0.0
            parsed.append({"chunk_id": chunk_id, "score": max(0.0, min(score, 1.0))})
        if parsed:
            return parsed
    except Exception:
        pass

    return [
        {
            "chunk_id": item.chunk.chunk_id,
            "score": round((item.bm25_score + item.embedding_score) / 2, 4),
        }
        for item in sorted_candidates
    ]


def _populate_chunk_embeddings(chunks: list[Chunk]) -> None:
    texts = [_chunk_index_text(chunk) for chunk in chunks]
    vectors = _embed_texts(texts)
    if not vectors or len(vectors) != len(chunks):
        return
    for chunk, vector in zip(chunks, vectors):
        chunk.embedding = vector


def _embed_query(query: str) -> list[float]:
    vectors = _embed_texts([query])
    if vectors:
        return vectors[0]
    return []


def _embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    client = _get_client()
    vectors: list[list[float]] = []
    batch_size = 16

    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        try:
            response = client.embeddings.create(
                model=settings.llm_embedding_model,
                input=batch,
            )
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors.extend([list(item.embedding) for item in ordered])
        except Exception:
            return [[0.0] * 8 for _ in texts]

    return vectors


def _chunk_index_text(chunk: Chunk) -> str:
    heading = " > ".join(chunk.heading_path) if chunk.heading_path else chunk.title
    return f"{chunk.filename}\n{heading}\n{chunk.text}".strip()


def _tokenize(text: str) -> list[str]:
    return [token.lower() for token in _TOKEN_RE.findall(text or "")]


def _top_indices(scores: list[float], top_k: int) -> list[int]:
    pairs = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)
    return [index for index, score in pairs[:top_k] if score > 0]


def _normalize_score(score: float, population: list[float]) -> float:
    if not population:
        return 0.0
    high = max(population)
    low = min(population)
    if math.isclose(high, low):
        return 1.0 if score > 0 else 0.0
    return (score - low) / (high - low)


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _extract_json(text: str) -> dict:
    cleaned = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()
    return json.loads(cleaned)


def _normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip().lower())


def _get_client() -> OpenAI:
    return OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )
