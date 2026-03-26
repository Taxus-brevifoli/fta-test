# SmartFTA-Dola Backend API 文档

## 1. 基本信息

- 服务标题：`SmartFTA-Dola API`
- 版本：`0.1.0`
- 默认监听：`http://0.0.0.0:8000`
- API 前缀：`/api/v1`
- 在线文档：
  - Swagger UI：`/docs`
  - ReDoc：`/redoc`
  - OpenAPI JSON：`/openapi.json`

---

## 2. 接口总览

| 模块 | 方法 | 路径 | 说明 |
|---|---|---|---|
| System | GET | `/health` | 健康检查 |
| Knowledge | POST | `/api/v1/knowledge/parse` | 上传并解析知识文档 |
| FTA | POST | `/api/v1/fta/generate` | AI 生成故障树 |
| FTA | POST | `/api/v1/fta/validate` | 故障树逻辑校验 |

---

## 3. 接口详情

### 3.1 健康检查

- 方法：`GET`
- 路径：`/health`
- Content-Type：`application/json`

**响应示例**

```json
{
  "status": "ok",
  "service": "SmartFTA-Dola Backend"
}
```

**状态码**

- `200`：服务正常

---

### 3.2 上传并解析知识文档

- 方法：`POST`
- 路径：`/api/v1/knowledge/parse`
- Content-Type：`multipart/form-data`
- 字段：
  - `files` (必填)：文件数组，支持 `.pdf`、`.docx`、`.txt`、`.md`
- 约束：
  - 至少上传 1 个文件
  - 单文件大小 <= `20MB`

**请求示例（curl）**

```bash
curl -X POST "http://localhost:8000/api/v1/knowledge/parse" \
  -H "accept: application/json" \
  -H "Content-Type: multipart/form-data" \
  -F "files=@/path/manual.pdf" \
  -F "files=@/path/notes.docx"
```

**成功响应示例**

```json
[
  {
    "filename": "manual.pdf",
    "text": "......解析出的全文......",
    "char_count": 12345,
    "preview": "前200字符预览"
  }
]
```

**状态码**

- `200`：解析成功
- `400`：未上传文件
- `413`：文件超过 20MB
- `415`：不支持的文件类型
- `422`：文件解析失败

---

### 3.3 AI 智能生成故障树

- 方法：`POST`
- 路径：`/api/v1/fta/generate`
- Content-Type：`application/json`

**请求体**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `top_event` | string | 是 | 顶事件描述 |
| `doc_text` | string | 是 | 已解析知识文档全文 |
| `extra_prompt` | string \| null | 否 | 额外约束提示词 |

**请求示例**

```json
{
  "top_event": "液压系统失压导致设备停机",
  "doc_text": "......知识文档文本......",
  "extra_prompt": "优先覆盖润滑与电气相关故障路径"
}
```

**成功响应结构**

| 字段 | 类型 | 说明 |
|---|---|---|
| `fta_data.attr` | object | 画布配置（颜色、尺寸、开关等） |
| `fta_data.nodeList` | array | 节点列表 |
| `fta_data.linkList` | array | 连线列表 |
| `source_summary` | string | AI 提取依据摘要 |

**`nodeList` 元素结构**

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 节点ID |
| `name` | string | 节点名称 |
| `type` | string | `"1"` 顶事件，`"2"` 中间事件，`"3"` 底事件 |
| `gate` | string | `"1"` AND，`"2"` OR |
| `event` | any \| null | 预留字段 |
| `transfer` | string | 预留字段 |
| `x` | number | 画布X坐标 |
| `y` | number | 画布Y坐标 |

**`linkList` 元素结构**

| 字段 | 类型 | 说明 |
|---|---|---|
| `sourceId` | string | 父节点ID |
| `targetId` | string | 子节点ID |
| `type` | string | 默认 `link` |
| `isCondition` | boolean | 默认 `false` |

**成功响应示例（节选）**

```json
{
  "fta_data": {
    "attr": {
      "background": "#fff",
      "fontColor": "#000",
      "width": 1920,
      "height": 1080
    },
    "nodeList": [
      {
        "id": "top-1",
        "name": "液压系统失压导致设备停机",
        "type": "1",
        "gate": "2",
        "event": null,
        "transfer": "",
        "x": 0,
        "y": 0
      }
    ],
    "linkList": []
  },
  "source_summary": "依据文档中的压力回路和阀组故障描述提取。"
}
```

**状态码**

- `200`：生成成功
- `400`：`top_event` 或 `doc_text` 为空
- `502`：AI 生成服务异常

---

### 3.4 故障树逻辑校验

- 方法：`POST`
- 路径：`/api/v1/fta/validate`
- Content-Type：`application/json`

**请求体**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `fta_data` | object | 是 | 与生成接口相同结构的故障树数据 |

**成功响应结构**

| 字段 | 类型 | 说明 |
|---|---|---|
| `issues` | array | 规则校验问题列表 |
| `ai_suggestions` | string | AI优化建议（LLM失败时为降级提示文本） |
| `is_valid` | boolean | 是否通过（仅基于 `error` 级别问题） |

**`issues` 元素结构**

| 字段 | 类型 | 说明 |
|---|---|---|
| `level` | string | `error` / `warning` / `info` |
| `message` | string | 问题描述 |
| `node_ids` | string[] | 相关节点ID列表 |

**成功响应示例**

```json
{
  "issues": [
    {
      "level": "warning",
      "message": "中间事件「油泵故障」没有子节点，应补充子原因。",
      "node_ids": ["mid-2"]
    }
  ],
  "ai_suggestions": "· 建议补充泵体磨损与吸油滤网堵塞分支\n· 建议确认 OR 门是否应改为 AND 门",
  "is_valid": true
}
```

**状态码**

- `200`：校验完成
- `502`：校验服务异常

---

## 4. 前端对接说明

- `generate` 返回的 `fta_data` 可直接用于前端故障树渲染（代码注释提到可直接供 `importJson()` 使用）。
- `validate` 的 `is_valid` 仅依据 `issues` 中是否存在 `level="error"`；`warning` 不会使其变为 `false`。
- 文档解析结果不在后端持久化，前端需自行保存 `text` 并在调用 `generate` 时传入 `doc_text`。

