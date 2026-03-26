"""
故障树逻辑校验服务。

分两层：
  1. 规则层（本地算法）：不调 LLM，快速检测结构性问题
  2. AI 层（LLM）：将故障树描述发给 LLM，获取自然语言优化建议
"""

from openai import OpenAI

from app.core.config import settings
from app.schemas.fta import FtaData, FtaValidateResponse, ValidationIssue

# ---------------------------------------------------------------------------
# 公共入口
# ---------------------------------------------------------------------------


def validate_fta(fta_data: FtaData) -> FtaValidateResponse:
    """
    对故障树进行完整校验，返回问题列表和 AI 建议。
    """
    issues = _rule_check(fta_data)
    is_valid = not any(i.level == "error" for i in issues)

    ai_suggestions = ""
    try:
        ai_suggestions = _ai_review(fta_data)
    except Exception:
        # LLM 调用失败不影响规则层结果
        ai_suggestions = "AI 建议获取失败，请检查 LLM 配置。"

    return FtaValidateResponse(
        issues=issues,
        ai_suggestions=ai_suggestions,
        is_valid=is_valid,
    )


# ---------------------------------------------------------------------------
# 规则层校验
# ---------------------------------------------------------------------------


def _rule_check(fta_data: FtaData) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    nodes = fta_data.nodeList
    links = fta_data.linkList

    if not nodes:
        issues.append(ValidationIssue(level="error", message="故障树为空，没有任何节点。"))
        return issues

    node_ids = {n.id for n in nodes}
    child_map: dict[str, list[str]] = {n.id: [] for n in nodes}
    parent_map: dict[str, list[str]] = {n.id: [] for n in nodes}

    # 构建邻接表，同时检查悬空引用
    for lk in links:
        if lk.sourceId not in node_ids:
            issues.append(ValidationIssue(
                level="error",
                message=f"连线引用了不存在的源节点 ID：{lk.sourceId}",
            ))
            continue
        if lk.targetId not in node_ids:
            issues.append(ValidationIssue(
                level="error",
                message=f"连线引用了不存在的目标节点 ID：{lk.targetId}",
            ))
            continue
        child_map[lk.sourceId].append(lk.targetId)
        parent_map[lk.targetId].append(lk.sourceId)

    # 顶事件唯一性
    top_events = [n for n in nodes if n.type == "1"]
    if len(top_events) == 0:
        issues.append(ValidationIssue(level="error", message="缺少顶事件（type=1）。"))
    elif len(top_events) > 1:
        issues.append(ValidationIssue(
            level="error",
            message=f"顶事件应只有一个，当前有 {len(top_events)} 个。",
            node_ids=[n.id for n in top_events],
        ))

    # 孤立节点（无父也无子，且不是顶事件）
    for n in nodes:
        if n.type != "1" and not parent_map[n.id] and not child_map[n.id]:
            issues.append(ValidationIssue(
                level="warning",
                message=f"节点「{n.name}」是孤立节点（既无父节点也无子节点）。",
                node_ids=[n.id],
            ))

    # 底事件不应有子节点
    for n in nodes:
        if n.type == "3" and child_map[n.id]:
            issues.append(ValidationIssue(
                level="error",
                message=f"底事件「{n.name}」不应有子节点。",
                node_ids=[n.id],
            ))

    # 中间事件应有至少一个子节点
    for n in nodes:
        if n.type == "2" and not child_map[n.id]:
            issues.append(ValidationIssue(
                level="warning",
                message=f"中间事件「{n.name}」没有子节点，应补充子原因。",
                node_ids=[n.id],
            ))

    # 环路检测（DFS）
    cycle_nodes = _detect_cycles(child_map)
    if cycle_nodes:
        issues.append(ValidationIssue(
            level="error",
            message="检测到循环依赖（环路），故障树必须是有向无环树。",
            node_ids=list(cycle_nodes),
        ))

    if not issues:
        issues.append(ValidationIssue(level="info", message="规则校验通过，未发现结构性问题。"))

    return issues


def _detect_cycles(child_map: dict[str, list[str]]) -> set[str]:
    """DFS 检测有向图中是否存在环，返回环路节点 ID 集合。"""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in child_map}
    cycle_nodes: set[str] = set()

    def dfs(nid: str) -> bool:
        color[nid] = GRAY
        for child in child_map.get(nid, []):
            if color[child] == GRAY:
                cycle_nodes.add(child)
                cycle_nodes.add(nid)
                return True
            if color[child] == WHITE and dfs(child):
                cycle_nodes.add(nid)
                return True
        color[nid] = BLACK
        return False

    for nid in child_map:
        if color[nid] == WHITE:
            dfs(nid)

    return cycle_nodes


# ---------------------------------------------------------------------------
# AI 层校验
# ---------------------------------------------------------------------------

_VALIDATE_SYSTEM_PROMPT = """\
你是一名工业设备故障树分析（FTA）专家，请对下面的故障树进行逻辑合理性审查。
审查重点：
- 故障传导路径是否合理
- 是否遗漏了重要的中间原因或底层原因
- 逻辑门（AND/OR）的使用是否恰当
- 事件命名是否清晰、专业

请用简洁的中文给出 3～5 条具体的优化建议，每条建议单独一行，以"·"开头。
"""


def _ai_review(fta_data: FtaData) -> str:
    """将故障树结构描述发给 LLM，获取自然语言优化建议。"""
    client = OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )

    node_map = {n.id: n for n in fta_data.nodeList}
    lines: list[str] = []

    # 找顶事件
    top_events = [n for n in fta_data.nodeList if n.type == "1"]
    if top_events:
        lines.append(f"顶事件：{top_events[0].name}")

    # 列出所有连线关系（父 → 子）
    lines.append("\n故障传导关系（父节点 → 子节点）：")
    for lk in fta_data.linkList:
        parent = node_map.get(lk.sourceId)
        child = node_map.get(lk.targetId)
        if parent and child:
            gate_label = "AND门" if parent.gate == "1" else "OR门"
            lines.append(f"  {parent.name}（{gate_label}） → {child.name}")

    # 底事件列表
    bottom_events = [n for n in fta_data.nodeList if n.type == "3"]
    if bottom_events:
        lines.append(f"\n底事件（根本原因）：{', '.join(n.name for n in bottom_events)}")

    tree_description = "\n".join(lines)

    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": _VALIDATE_SYSTEM_PROMPT},
            {"role": "user", "content": tree_description},
        ],
        temperature=0.3,
    )
    return response.choices[0].message.content or ""
