"""
知识文档解析与检索相关的数据结构。
"""

from typing import Optional

from pydantic import BaseModel, Field


class DocumentSection(BaseModel):
    id: str
    order: int = Field(0, description="文档内顺序")
    title: str = Field("", description="段落/章节标题")
    heading_path: list[str] = Field(default_factory=list, description="标题路径")
    page_start: Optional[int] = Field(None, description="起始页码（从 1 开始）")
    page_end: Optional[int] = Field(None, description="结束页码（从 1 开始）")
    paragraph_start: Optional[int] = Field(None, description="起始段序号（从 1 开始）")
    paragraph_end: Optional[int] = Field(None, description="结束段序号（从 1 开始）")
    text: str = Field(..., description="该段纯文本内容")


class ParsedDocument(BaseModel):
    filename: str
    filetype: str = Field("", description="文件类型后缀")
    text: str = Field(..., description="从文档解析出的纯文本内容")
    char_count: int = Field(..., description="文本字符数")
    preview: str = Field("", description="文本前 200 字预览")
    sections: list[DocumentSection] = Field(
        default_factory=list,
        description="保留结构信息的分段结果",
    )


class DocumentParseResponse(ParsedDocument):
    pass


class RetrievalOptions(BaseModel):
    bm25_top_k: int = Field(8, ge=1, le=30)
    embedding_top_k: int = Field(8, ge=1, le=30)
    rerank_top_k: int = Field(8, ge=1, le=20)
    final_evidence_k: int = Field(6, ge=1, le=20)
    bfs_max_depth: int = Field(2, ge=0, le=3)
    bfs_max_queries: int = Field(8, ge=1, le=20)
    context_window: int = Field(1, ge=0, le=3)


class EvidenceItem(BaseModel):
    chunk_id: str
    query: str = Field("", description="命中该证据的检索查询")
    filename: str
    score: float = 0.0
    title: str = Field("", description="所属标题")
    heading_path: list[str] = Field(default_factory=list)
    text: str = Field("", description="扩窗后的证据文本")
    page_start: Optional[int] = None
    page_end: Optional[int] = None


class RetrievalTraceItem(BaseModel):
    query: str
    depth: int
    evidence: list[EvidenceItem] = Field(default_factory=list)
    next_queries: list[str] = Field(default_factory=list)

