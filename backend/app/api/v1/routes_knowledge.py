"""
知识文档上传接口。

POST /api/v1/knowledge/parse
  - 上传一个或多个文档（PDF / DOCX / TXT），解析为纯文本后返回。
  - 不持久化存储，前端负责保存解析结果供后续生成使用。
"""

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.schemas.knowledge import DocumentParseResponse
from app.services import parser_service

router = APIRouter()

# 允许上传的文件类型
_ALLOWED_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}
# 单文件大小上限（字节），默认 20 MB
_MAX_FILE_BYTES = 20 * 1024 * 1024


@router.post(
    "/parse",
    response_model=list[DocumentParseResponse],
    summary="上传并解析知识文档",
    description="支持同时上传多个文件（PDF / DOCX / TXT），返回各文件解析出的纯文本内容。",
)
async def parse_documents(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="至少上传一个文件。")

    results: list[DocumentParseResponse] = []

    for file in files:
        filename = file.filename or "unknown"
        suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        if suffix not in _ALLOWED_SUFFIXES:
            raise HTTPException(
                status_code=415,
                detail=f"不支持的文件类型：{filename}（支持：{', '.join(_ALLOWED_SUFFIXES)}）",
            )

        content = await file.read()

        if len(content) > _MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"文件 {filename} 超过 20 MB 限制。",
            )

        # 文件解析在线程池中执行（避免阻塞事件循环）
        try:
            parsed_document: DocumentParseResponse = await run_in_threadpool(
                parser_service.parse_bytes,
                filename,
                content,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"解析文件 {filename} 失败：{exc}",
            ) from exc

        results.append(parsed_document)

    return results
