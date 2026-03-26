"""
SmartFTA-Dola 后端服务入口。

启动方式（在 backend/ 目录下执行）：
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

或直接运行本文件：
  python -m app.main
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import routes_fta, routes_knowledge
from app.core.config import settings

app = FastAPI(
    title="SmartFTA-Dola API",
    description="基于知识的工业设备故障树智能生成与辅助构建系统 — 后端服务",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# CORS（允许前端开发服务器跨域访问）
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# 路由注册
# ---------------------------------------------------------------------------

app.include_router(
    routes_knowledge.router,
    prefix="/api/v1/knowledge",
    tags=["Knowledge - 知识文档"],
)

app.include_router(
    routes_fta.router,
    prefix="/api/v1/fta",
    tags=["FTA - 故障树"],
)


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------

@app.get("/health", tags=["System"])
def health_check():
    return {"status": "ok", "service": "SmartFTA-Dola Backend"}


# ---------------------------------------------------------------------------
# 直接运行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=True,
    )
