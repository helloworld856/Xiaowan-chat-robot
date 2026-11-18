# 小晚AI角色对话软件

版本 `1.0.0`。小晚是支持多账号的 AI 聊天应用。前端使用 TypeScript 和 Vite，后端使用 FastAPI、LangGraph 和 MySQL，通过外部 API 调用 DeepSeek 或通义千问。每个账号拥有自己的对话，历史过长时自动压缩为摘要。

## 主要功能

- 根据用户意图和情绪生成角色化回复
- 通过管理员账号配置 DeepSeek 或通义模型
- 保存对话、加载本次运行的历史并自动生成历史摘要
- 支持三套前端主题，人设通过 `backend/persona_config/persona.json` 修改
- 加载失败或超时会显示提示；对话保存失败时仍显示回复并告知未保存

## 环境要求

- Python 3.10+
- Node.js 20.x（至少 20.19）或 22.12 及以上，以及 npm
- MySQL 8+
- DeepSeek 或通义千问 API Key

## 安装与配置

先安装并启动 MySQL，再在项目根目录执行：

```bash
python -m pip install -r requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
```

首次运行需要构建一次：后端启动时会检查 `frontend/dist`，即使使用Vite开发也需要该目录。构建产物不随仓库提交。

首次安装时复制 `backend/system_config.example.json` 为 `backend/system_config.json`，在 `database` 里填写 MySQL 的连接信息。全局配置支持 `//` 行注释，字段用途、单位和可选值写在对应字段旁。编辑后重启后端生效。数据库 `XiaoWan` 和所需表会在启动时创建。管理员可使用 `backend/.env.example` 创建 `backend/.env`，也可登录后在网页设置中保存模型和 API Key。

已有部署升级时，保留原来的 `backend/system_config.json`、`backend/.env`、`backend/settings.json` 和 `backend/user_data/`。首次启动会给旧版纯 JSON 全局配置补齐默认项和字段说明，保留原有值。旧管理员模型会迁入 `.env` 的 `ADMIN_PROVIDERS`。如果全局配置尚未创建，数据库密码和并发参数会从旧 `.env` 导入。

`auth` 管理会话有效期、密码长度和认证限流，`profile` 管理昵称长度，`defaults` 设定新账号的初始主题、快捷键和头像。`chat` 管理并发、消息长度、历史压缩和启动时的历史清理。`model_runtime` 管理模型连接验证并发，`logging` 管理日志轮转和启动清理。

## 聊天设置

管理员和普通用户都可以在网页设置中查看和更改厂商的 API 基础地址。模型选项来自 `system_config.json`，后端用同一份列表校验测试和保存请求。自定义地址须兼容 OpenAI 聊天接口，并包含所需的 `/v1` 等基础路径。普通用户的自定义地址还须使用公网 HTTPS。清空地址并保存可恢复全局配置中的厂商地址和协议。

“测试连接”只验证连接，不保存或切换；“保存并切换”验证成功后保存配置，并更新聊天、分析、摘要三个代理。验证或保存失败时保留当前模型。

输入习惯支持多行输入与 Enter / Ctrl+Enter 发送；Shift+Enter 始终换行，中文输入法确认文字不会触发发送。每条消息最多 8000 个字符。

管理员在 `backend/.env` 的 `ADMIN_PROVIDERS` 中按厂商各保存一条 `base_url`、`model_name` 和 `api_key`。普通用户的 `backend/user_data/<user_id>/model.json` 也按厂商保存这三个字段。后端使用对应账号的厂商配置，同厂商切换模型会共用地址和密钥，密钥留空时沿用已保存的值。旧普通用户文件在首次读取时迁移。真实配置文件由 Git 忽略。

在“设置 → 偏好”中切换主题或发送快捷键后，系统自动把两项都保存到当前账号的 `backend/user_data/<user_id>/preferences.json`。刷新、换浏览器和重启后都能恢复已保存的偏好。旧偏好文件缺少主题字段时使用默认白。

回复生成与显示沿用原有逻辑：自然分条，显示间隔按 `min(1000 + 100 × 当前回复长度, 5000)` 毫秒计算，最后一条回复后不等待。

侧边栏中的聊天回顾搜索当前页面已显示的对话，并支持点击定位原消息；不另外保存聊天记录，沿用原有启动清理和摘要压缩规则。

检查命令：`python -m unittest discover -s backend/tests -v`、`npm --prefix frontend test`、`npm --prefix frontend run typecheck`、`npm --prefix frontend run build`。测试不调用真实模型或业务数据库。

## 账户和启动

打开登录页可用用户名和密码注册普通账号。用户名支持 3–32 个英文字母、数字或下划线，唯一性检查不区分大小写，`admin` 及其大小写变体为保留用户名。注册和设置新密码时使用 6–15 个字符。账户和密码哈希保存在 MySQL。

从旧版升级时，首次启动将 `backend/auth_config/admin.json`、旧资料和发送快捷键导入管理员账号，已迁入的账户数据优先。首次安装的本地管理员用户名为 `admin`，初始密码为 `123456`。管理员可以在个人资料中修改密码。

登录 Cookie 默认有效期为 8 小时，可在 `auth.session_ttl_seconds` 修改。刷新页面可恢复登录，后端重启后需重新登录。退出只撤销当前登录，改密码后该账号的所有会话都会失效。同一浏览器配置下的标签页共享 Cookie。

菜单的“个人资料”可以修改昵称和选择六张系统头像，默认使用小猫。注册时创建 `backend/user_data/<user_id>/profile.json`，保存后刷新和重启都能恢复。头像素材位于 `frontend/public/avatars/system/`。

终端显示 Uvicorn 的启动进度和监听地址。已认证账号的运行日志只写入各自目录中的 `logs/app.log`，记录请求结果、回复条数、Token 和耗时。启动、匿名请求等系统日志写入 `backend/log_config/app.log`。运行 `run_serve.py` 启动时会清空系统和账号的当前日志。每个文件约 2 MB 时轮转，最多保留三份归档，重启时保留归档。

聊天、历史、人设、模型和设置接口均需要登录。开发环境使用下述 Vite 同源代理，生产使用后端托管前端；跨来源写请求会被拒绝。

开发时先启动后端：

```bash
cd backend
python run_serve.py
```

再在另一个终端启动前端：

```bash
cd frontend
npm run dev
```

访问 `http://localhost:5173`。Vite 会将 API 请求代理到 `127.0.0.1:8000`。如果修改了 `backend/system_config.json` 的 `server.port`，请同步更新 `frontend/vite.config.mts` 的代理端口。

如果只想启动一个服务，先构建前端：

```bash
npm --prefix frontend run build
cd backend
python run_serve.py
```

然后访问 `http://127.0.0.1:8000`。首次打开后，在页面设置中选择模型。

> 当前设计会在每次启动后端时清空历史对话和历史摘要。

后端使用单个 Uvicorn worker。每个账号首次发消息时创建自己的 LangGraph 和记忆管理器，不同账号可并行，同一账号逐轮处理。`system_config.json` 的 `chat.concurrency`、`chat.queue_size` 和 `chat.queue_timeout` 控制最多同时执行数、等待名额和排队秒数，默认分别为 4、8、2。排队失败时可以从原消息重试。

使用Vite开发时，前端修改会自动刷新；访问 `8000` 时，修改前端后需重新执行 `npm --prefix frontend run build`（在项目根目录）。修改后端后需重启后端服务。

## 升级已有账号的独立目录

升级已有服务器时，一并备份 MySQL、`backend/user_data/` 和服务端配置。保留 MySQL 中的账号 ID 与目录名的对应关系。

停掉旧版小晚，保留 MySQL 服务运行。用平时启动小晚的 Linux 用户，在项目根目录的后端 Python 环境执行：

```bash
python backend/migrate_user_data.py --dry-run
```

预览结果正常后，执行迁移并启动新版：

```bash
python backend/migrate_user_data.py --apply && python backend/run_serve.py
```

迁移补齐缺失文件，把原有发送快捷键导入各自的 `preferences.json`。资料和模型配置沿用原来的内容。异常会列出账号 ID，修复对应文件或权限后可再次执行。

回退前请用当前版本运行 `python backend/migrate_user_data.py --rollback-preferences`，将个人文件中的偏好回填到 MySQL，再启动旧版。
如果运行旧版期间修改了快捷键，下一次升级前先将对应的 `preferences.json` 移到备份位置，再执行迁移，读取旧版 MySQL 中的最新值。
