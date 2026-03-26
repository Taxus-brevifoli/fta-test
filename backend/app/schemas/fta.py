"""
故障树相关的请求/响应数据结构。

字段命名与前端 ftaAdapter.js 中的 business JSON 格式保持一致：
  nodeList[].type : "1"=顶事件, "2"=中间事件, "3"=底事件
  nodeList[].gate : "1"=AND门,   "2"=OR门
"""

from typing import Any, List, Optional

from pydantic import BaseModel, Field, model_validator

from app.schemas.knowledge import EvidenceItem, ParsedDocument, RetrievalOptions, RetrievalTraceItem


class FtaNode(BaseModel):
    id: str
    name: str
    type: str = "2"  # "1" | "2" | "3"
    gate: str = "2"  # "1"=AND | "2"=OR
    event: Optional[Any] = None
    transfer: str = ""
    x: float = 0.0
    y: float = 0.0


class FtaLink(BaseModel):
    sourceId: str
    targetId: str
    type: str = "link"
    isCondition: bool = False


class FtaAttr(BaseModel):
    background: str = "#fff"
    fontColor: str = "#000"
    eventColor: str = "#000"
    eventFillColor: str = "#fff"
    gateColor: str = "#000"
    gateFillColor: str = "#fff"
    linkColor: str = "#456"
    eventCode: bool = True
    eventProbability: bool = False
    containerX: float = 0
    containerY: float = 0
    width: float = 1920
    height: float = 1080


class FtaData(BaseModel):
    attr: FtaAttr = Field(default_factory=FtaAttr)
    nodeList: List[FtaNode] = Field(default_factory=list)
    linkList: List[FtaLink] = Field(default_factory=list)


class FtaGenerateRequest(BaseModel):
    top_event: str = Field(..., description="顶事件描述，即用户指定要分析的根本故障现象")
    documents: list[ParsedDocument] = Field(
        default_factory=list,
        description="结构化解析后的知识文档数组",
    )
    doc_text: str = Field("", description="已解析的纯文本内容（兜底兼容字段）")
    extra_prompt: Optional[str] = Field(None, description="用户附加的生成要求（可选）")
    retrieval_options: Optional[RetrievalOptions] = Field(
        None,
        description="检索参数（可选）",
    )

    @model_validator(mode="after")
    def validate_knowledge_input(self):
        if not self.documents and not self.doc_text.strip():
            raise ValueError("documents 与 doc_text 至少提供一个。")
        return self


class FtaGenerateResponse(BaseModel):
    fta_data: FtaData
    source_summary: str = Field("", description="AI 提取依据摘要（溯源说明）")
    evidence_summary: str = Field("", description="RAG 检索证据摘要")
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    retrieval_trace: list[RetrievalTraceItem] = Field(default_factory=list)


class FtaValidateRequest(BaseModel):
    fta_data: FtaData


class ValidationIssue(BaseModel):
    level: str  # "error" | "warning" | "info"
    message: str
    node_ids: List[str] = Field(default_factory=list)


class FtaValidateResponse(BaseModel):
    issues: List[ValidationIssue] = Field(default_factory=list)
    ai_suggestions: str = Field("", description="AI 给出的自然语言优化建议")
    is_valid: bool = True

