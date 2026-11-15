# Doro AI → ChatGPT MCP（独立试用服务）

本分支与 `master` 的沐雪服务相互独立，**不包含 API Key 或 OAuth 密码**。

- 上游 API：`https://api.doroai.app/v1`（Responses API）
- 测试模型：`gpt-6.1-sol`（第三方平台提供的模型 ID，实际能力尚待验证）
- MCP 工具：`ask_doro`
- Render 服务名称：`doro-chatgpt-mcp`，接口 `/mcp`
- 运行时：Python；构建 `pip install -r requirements.txt`；启动 `uvicorn server:app --host 0.0.0.0 --port $PORT`

## Render 中私下填写

- `OPENAI_API_KEY`：Doro 后台重新生成、未在聊天里公开过的密钥，建议设置较小额度限制。
- `OAUTH_LOGIN_PASSWORD`：16 字符以上的强密码，不与 Doro API Key 相同。
- `DORO_BASE_URL=https://api.doroai.app/v1`
- `DORO_MODEL=gpt-6.1-sol`

先检查 `/health` 返回 `status: ready`；再在 ChatGPT 新建自定义 MCP 服务，选择 OAuth，填入新服务的 `/mcp` URL。

这是个人测试服务，Render 免费服务可能休眠。第三方可能记录传输文本并产生 API 计费；不要向外部模型提交私人资料或完整聊天历史。
