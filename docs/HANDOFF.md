# 小晚项目交接

更新于 2026-11-17。开发分支为 `codex/development`，远程仓库为 `helloworld856/Xiaowan-chat-robot`。在开发分支继续工作，合并主分支前先确认。Windows 工作区为 `E:\html\小晚`，服务器检出目录为 `~/xiaowan`。

## 已实现的功能

- 支持登录及注册。用户名不区分大小写且唯一，`admin` 是保留用户名。新密码为 6–15 个字符。退出登录前需要确认。个人资料支持昵称和六张系统头像，默认小猫。
- 管理员使用 `backend/.env` 的厂商 API Key，保存的模型厂商、名称和自定义地址在 `backend/settings.json`。普通账号注册时创建个人模型配置，填写自己的 API Key 后才能聊天，普通账号仅连接厂商官方端点。
- 每个账号首次聊天时创建独立的 LangGraph 和记忆管理器。单个 Uvicorn worker 内，不同账号可并行，同一账号逐轮处理。`.env` 默认允许同时处理 4 个请求，另有 8 个等待名额，排队超时为 2 秒。
- 回复自然分成 1–5 个气泡。每条之间的显示延迟按当前气泡字数计算，最后一条之后即可继续操作。发送失败使用提示弹窗，原消息旁显示红色感叹号，悬浮提示“发送失败”。
- 侧栏使用一个关闭按钮，聊天回顾可以搜索并定位当前页面已加载的聊天。输入框初始为单行，随内容增高。输入区只保留输入框和操作按钮。设置页支持 Enter 或 Ctrl+Enter 发送，Shift+Enter 换行，设置项沿用横线分隔。聊天节奏由代码计算，回复风格由提示词控制。

## 如需定位代码

| 位置 | 负责的功能 |
| --- | --- |
| `backend/app/application.py`、`backend/app/api/` | 应用装配与 HTTP 接口 |
| `backend/app/chat/` | 账号聊天状态、并发队列、工作流与历史 |
| `backend/app/accounts/`、`backend/app/api/auth_middleware.py` | MySQL 账号、会话认证和个人目录 |
| `backend/app/llm/` | 模型配置、模型输出解析、本地分词与 Token 计数 |
| `backend/run_serve.py`、`backend/app/account_logging.py` | 服务启动提示和文件日志路由 |
| `frontend/src/ts/chat.ts`、`utils.ts`、`review.ts`、`settings.ts` | 聊天发送、气泡节奏、回顾和设置 |

## 数据与日志约定

- MySQL 保存账号、密码哈希、聊天历史和摘要。后端进程重启会清空聊天历史与摘要，并让现有登录会话失效。这是当前约定。生成摘要时会按原逻辑压缩部分旧聊天原文。
- `backend/user_data/<用户ID>/` 下的 `profile.json` 和 `preferences.json` 保存资料与发送快捷键，普通账号另有 `model.json`。目录使用 MySQL 的账号 ID。备份时将 MySQL、用户目录和服务端配置一并保存。
- 终端显示 Uvicorn 启动进度和监听地址。系统日志写入 `backend/log_config/app.log`，账号运行日志写入该账号目录的 `logs/app.log`。运行 `backend/run_serve.py` 启动时会清空这些当前日志，保留轮转归档。模型 JSON 容错失败时，账号日志会标明「消息分析」或「回复生成」，可用的纯文本作为单条回复发送。
- `backend/.env`、账号目录、运行日志和 `frontend/dist/` 由运行环境维护。`backend/settings.json` 和 `backend/.env.example` 跟随 Git；实际密钥放在 `.env` 或个人 `model.json`。

## Ubuntu 服务器

已知部署为 Ubuntu Server 24.04、MySQL 8、Conda 环境 `xiaowan`，使用 Nginx 和公网 IP。后端默认监听 `127.0.0.1:8000`，Nginx 对外提供入口。用户目前用 Xshell 执行 `python backend/run_serve.py` 启动前台进程。

更新代码，在服务器执行：

```bash
cd ~/xiaowan &&
git -c http.version=HTTP/1.1 pull --ff-only origin codex/development &&
git log -1 --oneline
```

服务端若已运行 `b3c9d3e`，拉取本次清理提交后可继续使用现有进程。更新后端功能时，在原启动窗口按 `Ctrl+C`，然后执行 `cd ~/xiaowan && conda activate xiaowan && python backend/run_serve.py`。拉取中如有 `requirements.txt` 更新，在重启前执行 `python -m pip install -r requirements.txt`。前端代码或依赖变更时执行 `npm --prefix frontend ci && npm --prefix frontend run build`。

从用户目录改造前的旧版升级时，先备份并停掉应用，保留 MySQL 运行，再执行 `python backend/migrate_user_data.py --dry-run`。确认异常数为 0 后执行 `python backend/migrate_user_data.py --apply`，再启动后端。回退个人偏好见 [README](../readme.md#升级已有账号的独立目录)。

另开 SSH 窗口，用 `curl -i --max-time 5 http://127.0.0.1:8000/auth/session` 检查后端。未登录时 `HTTP 200` 和 `{"user":null}` 是正常结果。系统日志可用 `tail -n 30 ~/xiaowan/backend/log_config/app.log` 查看。之前已确认服务器的本机接口返回 200，实际部署提交以服务器上的 `git log -1` 为准。

## 本地验证

```text
python -m unittest discover -s backend/tests -v
npm --prefix frontend test
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

本机可使用 `D:\.conda\langgraph_env\python.exe`。MySQL 集成测试仅在显式设置 `XIAOWAN_TEST_MYSQL_PORT` 并提供隔离实例时运行。生产部署的数据库和模型密钥由服务器管理。
