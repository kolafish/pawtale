# AI 服务架构文档

## 概述

Pawtale 使用多 AI 模型支持文本生成，通过工厂模式统一管理不同的 AI 服务提供商。

## 架构设计

### 1. 服务层次

```
app/services/
├── ai_base.py          # Provider 抽象基类和公共方法代理
├── ai_story.py         # 故事生成 prompt、解析和分支选项
├── ai_revision.py      # 文本修订 prompt
├── ai_project_analysis.py # 项目分析 prompt、重试和结构化清洗
├── ai_types.py         # AITextClient 协议
├── ai_factory.py       # 工厂函数，根据模型名返回服务实例
├── ai_claude.py        # Claude 服务实现（通过代理）
├── ai_text.py          # DeepSeek 服务实现
├── ai_volc.py          # 火山方舟服务实现
└── ai_debug.py         # 调试信息构建工具
```

### 2. 统一接口（AITextServiceBase）

`AITextServiceBase` 定义 provider 合约，具体模型服务只需要实现 `_call_llm()`、`provider_name` 和 `model_name`。
产品能力由独立 use-case 模块实现，基类只保留薄代理：

- `generate_story()` - 生成故事内容和分支选项
- `revise_chapter()` - 根据修改意见重写内容
- `analyze_project_content()` - 从项目手稿提取人物、大纲和设定

### 3. 工厂模式

```python
from app.services import get_ai_service

# 根据模型名获取服务实例
ai_service = get_ai_service("claude-opus-4")  # 或 "deepseek-v4-flash", "deepseek-v4-pro", "doubao-seed-2-0-pro-260215"
result = await ai_service.generate_story(...)
```

## Claude 代理配置

### 问题背景

项目使用的 Claude 代理（cursor.scihub.edu.kg）采用 **OpenAI 兼容格式**，但有以下限制：

1. **忽略 system role 消息** - 代理会丢弃 `{"role": "system"}` 的消息
2. **模型 ID 格式** - 必须使用短格式（`claude-opus-4-6`，而非 `claude-opus-4-20250514`）
3. **认证方式** - 使用 `Authorization: Bearer {ANTHROPIC_AUTH_TOKEN}`（token 以 `cr_` 开头）

### 解决方案

#### 方案 1：合并 system prompt 到 user message（Claude 专用）

```python
# ai_claude.py 中的实现
async def _call_claude(
    self,
    system_prompt: str,
    user_prompt: str,
    ...
):
    # 将 system prompt 合并到 user message 中
    response = await client.post(
        f"{self.base_url}/v1/chat/completions",
        json={
            "model": self.model,
            "messages": [
                {"role": "user", "content": f"{system_prompt}\n\n{user_prompt}"},
            ],
            ...
        }
    )
```

#### 方案 2：使用简短 system prompt（DeepSeek 原生 API）

```python
# ai_text.py 中的实现
# DeepSeek 原生 API 支持 system role，但保持简洁
response = await client.post(
    f"{self.base_url}/v1/chat/completions",
    json={
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": "你是一位专业的文字编辑。"},
            {"role": "user", "content": user_prompt},
        ],
        ...
    }
)
```

### 关键原则

1. **任务导向的 prompt** - 避免角色定义（"你是..."），直接描述任务
2. **所有指令放在 user message** - 确保代理能看到完整指令
3. **统一处理** - 所有 AI 调用方法都遵循相同模式

## 服务层调用

### 项目文档 AI 功能

```python
# backend/app/services/projects/ai.py

async def ai_continue_document(...):
    """AI 续写文档"""
    ai_service = get_ai_service(req.ai_model or "claude-opus-4")
    result = await ai_service.generate_story(
        characters=[],
        scene={...},
        previous_content=doc.content[-4000:],
        custom_direction=req.instruction,
        debug=req.debug,
    )
    # 追加生成内容到文档
    doc.content = f"{doc.content}\n\n{result['content']}"
    await db.commit()

@router.post("/{project_id}/documents/{document_id}/ai-revise")
async def ai_revise_document(...):
    """AI 修改文档（返回建议，不自动保存）"""
    ai_service = get_ai_service(req.ai_model or "claude-opus-4")
    result = await ai_service.revise_chapter(
        original_content=req.selected_text or doc.content,
        revision_instruction=req.instruction,
        characters=[],
        debug=req.debug,
    )
    # 返回修改建议，由前端用户决定是否接受
    return {"revised_text": result["content"], ...}
```

## 调试模式

所有 AI 方法支持 `debug=True` 参数，返回完整的请求/响应信息：

```python
result = await ai_service.revise_chapter(..., debug=True)
# result["debug_info"] 包含：
# - request_info: 模型、温度、token 限制、完整 prompt
# - response_text: AI 原始返回文本
# - response_json: 完整 API 响应 JSON
```

## 环境变量配置

```bash
# backend/.env

# Claude API（通过代理）
ANTHROPIC_AUTH_TOKEN=cr_xxx
ANTHROPIC_BASE_URL=https://cursor.scihub.edu.kg

# DeepSeek API
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_THINKING_TYPE=disabled

# 火山方舟 API
VOLC_ARK_API_KEY=replace-with-ark-api-key
VOLC_ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
VOLC_ARK_MODEL=doubao-seed-2-0-pro-260215
VOLC_ARK_THINKING_TYPE=disabled

# 默认模型
DEFAULT_AI_MODEL=claude-opus-4  # 可选: deepseek-v4-flash, deepseek-v4-pro, doubao-seed-2-0-pro-260215, claude-opus-4, claude-sonnet-4
```

## 添加新 AI 服务

1. 创建新服务类继承 `AITextServiceBase`
2. 实现 `_call_llm()`、`provider_name` 和 `model_name`
3. 在 `ai_factory.py` 中注册新模型
4. 更新环境变量配置

```python
# 示例：添加 GPT-4 支持
class GPT4TextService(AITextServiceBase):
    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return "gpt-4"

    async def _call_llm(self, system_prompt: str, user_prompt: str, ...):
        # 只实现模型传输逻辑；prompt/use-case 复用现有模块
        ...

# ai_factory.py
def get_ai_service(model: str) -> AITextServiceBase:
    if model.startswith("gpt-4"):
        return GPT4TextService()
    # ...
```

## 常见问题

### Q: 为什么 Claude 不用 system role？
A: 项目使用的代理会忽略 system role 消息。将 system prompt 合并到 user message 是兼容性解决方案。

### Q: DeepSeek 为什么还用 system role？
A: DeepSeek 使用原生 API，支持 system role。但我们保持 system prompt 简短，主要指令放在 user message。默认模型为 `deepseek-v4-flash`，可切换 `deepseek-v4-pro`；并通过 `DEEPSEEK_THINKING_TYPE=disabled` 保持与旧 `deepseek-chat` 接近的非思考行为。

### Q: 火山方舟为什么单独一个 provider？
A: 火山方舟使用 Responses API，base URL、API key、模型 ID 和 thinking 参数独立于 DeepSeek。`VOLC_ARK_THINKING_TYPE` 默认 `disabled`，避免回忆录短追问被隐藏推理 token 截断；需要深度推理时可以按任务单独开启。

### Q: 如何切换默认模型？
A: 修改 `.env` 中的 `DEFAULT_AI_MODEL`，或在前端 UI 中选择模型。

### Q: 导出功能也用 AI 吗？
A: 导出功能（`/api/projects/{id}/export`）只是纯文本拼接，不调用 AI。
