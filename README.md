# 龙傲 AI → ChatGPT MCP（独立试用服务）

这是为 https://ai.laai0.cc/keys 建立的独立 MCP 网关，和 Doro、沐雪服务分离。**仓库中不包含 API Key 或登录密码。**

网站 /keys 是密钥管理页，不代表 API Base URL 已确认。尝试的 OpenAI 兼容根地址为 `https://ai.laai0.cc/v1`，**尚未获得该站官方文档确认**；如后台有不同的 API 接口地址，应以后台为准。

- MCP 工具：`ask_laai`
- OAuth 登录保护；个人专用，授权后第三方 API 可能对发送的文本收费
- API 模式：`LAAI_API_MODE=chat_completions` 或 `responses`
- 默认：非流式 Chat Completions 短文本请求；模型名称请按龙傲 AI 后台实际模型 ID 填写
- Python / Render：构建 `pip install -r requirements.txt`，启动 `uvicorn server:app --host 0.0.0.0 --port $PORT`

## Render Environment（仅用户本人填写）
- `LAAI_API_KEY`：在平台新创建且未公开的测试 Key，至少 24 字符，并建议设置额度限制
- `OAUTH_LOGIN_PASSWORD`：用户自己设置的 16 字符以上强密码，不能与 API Key 相同
- `LAAI_MODEL`：该平台具体可调用模型 ID；不沿用 Doro 的名称推断
- `LAAI_BASE_URL`：平台提供的真实 OpenAI 兼容 API 地址，通常到 `/v1`，不要填 `/keys`
- `PUBLIC_BASE_URL`：该 Render 服务自己的 `https://...onrender.com` 地址
- `LAAI_API_MODE=chat_completions`，如平台仅支持 Responses，则改成 `responses`

用户设置完毕、`/health` 显示 `ready` 且 API 文本调用成功后，才能认为模型已连接；健康检查只能说明变量长度合格，不代表 API Key 有实际生成权限。不要在聊天、截图或 GitHub 中粘贴密钥。