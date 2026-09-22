# Pawtale AI Provider 架构

## 边界

Pawtale 将“provider transport”和“产品 AI 用例”分开：

```text
产品 service / prompt
        ↓
AITextServiceBase
        ↓
AIServiceFactory
        ├─ DeepSeekTextService
        ├─ ClaudeTextService
        └─ VolcArkTextService
```

provider 子类只负责认证、payload、HTTP/SSE 解析、超时和并发限制。小说续写、文本修改、项目分析、回忆录访谈策略、素材整理、章节生成和全书文案分别由对应 use-case service 维护。

## 当前文本模型

| 产品值 | provider 实现 |
| --- | --- |
| `deepseek-flash` | DeepSeek V4.1 Flash OpenAI-compatible chat API |
| `deepseek-v4-pro` | DeepSeek OpenAI-compatible chat API |
| `doubao-seed-2-0-pro-260215` | Volc Ark Responses API |
| `claude-fable-5` | 产品选择器已下线；仅保留兼容旧直连请求，历史回忆录项目值会回落到 `deepseek-flash` |
| `claude-opus-4-8` | Claude Opus 4.8 OpenAI-compatible relay |
| `claude-sonnet-4` | Claude Sonnet 4.6 兼容代理 |

新项目和普通用户默认使用 `deepseek-flash`。管理员可以在 Web 端选择当前可用模型；Claude Fable 5 不再出现在模型选择器中。`AIServiceFactory.get_service(model)` 负责产品模型值到 transport 的映射，未知值会明确报错。
历史值 `deepseek-v4-flash` 仍由后端兼容解析到 `deepseek-flash`，供已有项目和待重试任务平滑迁移。

## 统一接口

`AITextServiceBase` 定义：

- `_call_llm(...)`：非流式文本调用；
- `stream_llm(...)`：支持 provider 的流式文本调用；
- provider/model 元数据；
- debug 信息构造；
- JSON payload 提取；
- 通用写作 use-case 的薄入口。

新增产品能力时，应在独立 service 中组织 prompt、领域输入和输出 schema，不继续扩大 provider 类。

## Provider 差异

### DeepSeek

- 使用 `/v1/chat/completions`；
- 支持 system role；
- 支持 SSE streaming；
- `DEEPSEEK_THINKING_TYPE` 控制 thinking payload。

### Claude 兼容代理

- 普通非流式文本使用 OpenAI-compatible `/v1/chat/completions`；
- 结构化输出使用同一接口的强制函数调用，显式关闭流式输出，只解析
  `tool_calls[].function.arguments`，再通过业务 Pydantic schema 校验；
- 实时流式文本使用 Anthropic-compatible `/v1/messages` SSE；
- Base URL 兼容带或不带 `/v1`；
- 当前代理可能忽略 system role，因此 transport 将 system 与 user prompt 合并；
- 响应解析兼容 OpenAI message、Anthropic content blocks 和纯文本 content。

### Volc Ark

- 使用火山方舟 Responses API；
- 文本模型与图片模型分别配置；
- 回忆录分享视频可以使用图片模型生成背景，但是否启用由后端开关控制。

## 并发、错误和可观测性

- 文本 provider 共享 `AI_TEXT_MAX_CONCURRENT_REQUESTS` 限流；
- TTS、forced alignment 和图片生成拥有独立并发限制；
- provider 超时、HTTP 错误或空响应必须显式失败；
- 回忆录后台任务在失败时保留原始素材，并记录 job/automation event；
- debug 模式可返回请求模型、温度、token 上限、prompt 和原始响应，不能包含 secret。

## 回忆录 AI 用例

回忆录不使用一个万能 prompt。主要边界包括：

- `voice_followup_ai.py` / `narrative/interview_policy*.py`：访谈回应与受控动作；
- `material_organization.py`：素材归入 Section；
- `section_digests.py` / `chapter_digests.py` / `book_reviews.py`：分层派生审阅；
- `chapter_generation.py` / `book_manuscript_ai.py`：章节和全书候选正文；
- `transcript_cleanup_editor.py` / `dialect_profile.py`：转写校正；
- `share_video_scene_planner.py`：分享视频场景规划。

领域逻辑负责权限、输入签名、素材 allowlist、版本和用户决定；模型不能自行删除素材、覆盖隐私决定或把未讲述内容写成事实。

## 配置

完整配置见 `backend/.env.example` 和 `app/config.py`。provider key 只允许存在于后端运行环境。

常用变量：

```text
DEFAULT_AI_MODEL
AI_TEXT_MAX_CONCURRENT_REQUESTS

DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL
DEEPSEEK_MODEL
DEEPSEEK_THINKING_TYPE

ANTHROPIC_AUTH_TOKEN
ANTHROPIC_BASE_URL

VOLC_ARK_API_KEY
VOLC_ARK_BASE_URL
VOLC_ARK_MODEL
VOLC_ARK_THINKING_TYPE
```

## 新增 Provider

1. 实现 `AITextServiceBase` 的 provider 名称、模型名称和 `_call_llm`；
2. 如支持流式文本，实现 `stream_llm`；
3. 在 `AIServiceFactory` 增加显式模型映射；
4. 在前端/小程序模型选项中增加同一个产品值；
5. 增加 transport、错误解析、并发限制和 factory 测试；
6. 更新 `.env.example`，但不提交真实 key。
