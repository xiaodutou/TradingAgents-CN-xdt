# 本地部署

源码来源：https://github.com/xiaodutou/TradingAgents-CN-xdt ，main 分支压缩包，下载于 2026-10-04。
GitHub Git 连接失败后使用官方 codeload 压缩包，因此目录不含 Git 历史。

访问：http://localhost:8088
管理员：admin / admin123

前后端均从本目录源码构建。部署文件为 compose.local.yml，项目名称 tradingagents-xdt。
数据库与缓存使用独立 Docker 卷。服务绑定 127.0.0.1:8088。
本机原有 tradingagents-demo 服务继续保留。

在本目录运行 PowerShell：

```powershell
.\manage-local.ps1 start
.\manage-local.ps1 status
.\manage-local.ps1 stop
.\manage-local.ps1 logs
.\manage-local.ps1 rebuild
```

运行前需启动 Docker Desktop。服务配置了 unless-stopped 自动重启。
配置文件为 .env；修改后运行 start 以重新创建服务。
登录后在配置管理中填写模型 API Key、模型名称和端点；Tushare 等数据源也需自己的凭证。
未配置模型密钥前，部署与登录可用，但无法完成 AI 分析。
已生成本地 JWT/CSRF 密钥。Nginx 健康检查使用 127.0.0.1，避免 localhost 的 IPv6 解析造成误报。

部署提交：baa664a26531124e019acb251aeb42cb07ed4739（与 2026-10-04 核对时远端 main 最新提交一致）。
已核对容器内 705 个后端相关文件，SHA256 均与本地源码一致。

## ChatGPT Plus 接入（本地修改）

配置入口：http://localhost:8088/settings/config?tab=chatgpt-plus

1. 点击 Continue with ChatGPT，在 OpenAI 官方页面登录并授权会员额度。
2. 回到配置页，选择账号实际可用的模型，点击“用于股票分析”。
3. 测试连接后刷新分析页；单股和批量分析的模型区域会显示“使用 ChatGPT 会员额度”。

此集成使用官方动态客户端注册、PKCE、OIDC 身份校验和流式 Responses 接口。
它不使用普通 API Key，也不会在会员额度不可用时自动切换到付费 API。
授权回调：http://127.0.0.1:1455/auth/callback，1455 端口仅绑定本机。
访问、刷新和 ID 凭证均在独立 Docker 卷 chatgpt_credentials 中加密存储；模型配置只保存非秘密的授权模式标记。
保留 .env 中 JWT_SECRET，改动该值会使现有加密凭证无法解密；不要将凭证卷公开或随配置导出。
后端需保持单个进程，以串行处理刷新令牌轮换。当前 Compose 使用一个 uvicorn 进程。
断开授权后，已有会员模型配置仍保留，但调用会提示重新授权。
当前适配支持分析所需的文本、函数工具调用及推理上下文，不支持此接口限制的托管工具或音视频。

官方文档：
- https://developers.openai.com/siwc/token-sharing-open-source/sign-in
- https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
- https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations

离线验证：将 tests/test_chatgpt_plan.py 复制到容器 /tmp，运行 python -m unittest discover -s /tmp -p test_chatgpt_plan.py。
实际会员调用需账号本人完成登录授权后验证。
