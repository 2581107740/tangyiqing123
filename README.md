# 沐雪 AI → ChatGPT（MCP）

本仓库存放部署到 Render 的私用 MCP 桥接服务源码；**不包含任何 API 密钥或登录密码**。

- 第三方上游：`https://v2.muxueai.pro`（Responses API）
- 默认模型：`gpt-5.6-sol`
- MCP 工具：`ask_muxue`
- Render 免费 Python Web Service，HTTPS 路径：`/mcp`

## 必须在 Render 私下配置

- `OPENAI_API_KEY`：重新生成、且未在聊天或仓库公开过的沐雪 API Key。
- `OAUTH_LOGIN_PASSWORD`：仅在 Render 环境变量中设置的 16 字符以上强密码，不能与 API Key 相同。
- 可选：`MUXUE_BASE_URL=https://v2.muxueai.pro`、`MUXUE_MODEL=gpt-5.6-sol`。

没有上述两项密钥时，接口返回 **503 setup_required**，无法调用上游。

## Render 构建

运行时：Python；免费套餐；构建命令：`pip install -r requirements.txt`；启动命令：`uvicorn server:app --host 0.0.0.0 --port $PORT`。

确认 `https://<Render 域名>/health` 显示 `ready` 后，才能在 ChatGPT 的「添加自定义 MCP 服务器」填写 `https://<Render 域名>/mcp` 并使用 OAuth 登录。

这是个人测试版的 OAuth 动态注册与 PKCE 连接服务，尚不适合多人生产使用。Render 免费服务可能休眠，第三方模型调用可能产生费用。仅在你明确要求 `ask_muxue` 时向第三方发送文本，避免提交敏感信息。

