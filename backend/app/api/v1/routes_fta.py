"""
故障树核心接口。

POST /api/v1/fta/generate  - AI 生成故障树
POST /api/v1/fta/validate  - 逻辑校验当前故障树
"""

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from app.schemas.fta import (
    FtaGenerateRequest,
    FtaGenerateResponse,
    FtaValidateRequest,
    FtaValidateResponse,
)
from app.services import ai_service, validator_service

router = APIRouter()


@router.post(
    "/generate",
    response_model=FtaGenerateResponse,
    summary="AI 智能生成故障树",
    description=(
        "传入知识文档解析文本和顶事件描述，"
        "由 AI 自动提取故障关联关系并生成符合 FTA 规范的故障树。"
        "返回的 fta_data 可直接由前端 importJson() 渲染。"
    ),
)
async def generate_fta(body: FtaGenerateRequest):
    if not body.top_event.strip():
        raise HTTPException(status_code=400, detail="顶事件描述不能为空。")

    try:
        response = await run_in_threadpool(
            ai_service.generate_fta_response,
            body.top_event,
            body.documents,
            body.doc_text,
            body.extra_prompt or "",
            body.retrieval_options,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"AI 生成失败：{exc}",
        ) from exc

    return response


@router.post(
    "/validate",
    response_model=FtaValidateResponse,
    summary="故障树逻辑校验",
    description=(
        "对当前故障树进行结构性规则校验（环路、孤立节点等）"
        "并调用 AI 给出优化建议。"
    ),
)
async def validate_fta(body: FtaValidateRequest):
    try:
        result = await run_in_threadpool(validator_service.validate_fta, body.fta_data)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"校验服务异常：{exc}",
        ) from exc

    return result
