# 小晚AI角色对话软件

版本 `1.0.0`。小晚是支持多账号的 AI 聊天应用。前端使用 Vue 3、TypeScript 和 Vite，后端使用 FastAPI、LangGraph 和 MySQL，通过外部 API 调用 DeepSeek 或通义千问。每个账号拥有自己的对话，历史过长时自动压缩为摘要。

## 主要功能

- 根据用户意图和情绪生成角色化回复
- 管理员和普通账号可分别配置自己的 DeepSeek 或通义模型
- 保存对话、加载本次运行的历史并自动生成历史摘要
- 支持默认白和三套彩色主题，人设通过 `backend/persona_config/persona.json` 修改
- 加载失败或超时会显示提示；对话保存失败时仍显示回复并告知未保存

## 项目结构

```text
小晚/
├── backend/
│   ├── run_serve.py                # 后端启动入口
│   ├── app/
│   │   ├── main.py                 # 创建应用并托管前端构建产物
│   │   ├── application.py          # 装配路由、账号服务和启动流程
│   │   ├── system_config.py         # 全局配置校验和迁移
│   │   ├── static_assets.py         # 前端静态资源缓存策略
│   │   ├── api/                    # HTTP 路由与认证中间件
│   │   ├── accounts/               # 账号、会话、个人资料与偏好
│   │   ├── chat/                   # 聊天调度、LangGraph 工作流和历史摘要
│   │   ├── database/               # MySQL 连接池和表结构初始化
│   │   └── llm/                    # 厂商目录、模型配置、连接验证和模型客户端
│   ├── persona_config/             # 小晚的人设配置
│   ├── utils/deepseek_tokenizer/    # 本地 Token 计数资源
│   ├── tests/                      # 后端测试
│   ├── .env.example                # 运行环境变量模板
│   └── system_config.example.json  # 全局配置模板
├── frontend/
│   ├── index.html                  # 前端页面入口
│   ├── src/main.ts                 # Vue 应用入口
│   ├── src/components/             # 聊天、登录、设置和个人资料组件
│   ├── src/composables/            # 会话和聊天状态
│   ├── src/ts/                     # API 客户端、类型和共享工具
│   ├── src/styles/                 # 页面样式和主题
│   ├── public/                     # 头像、网站图标等静态资源
│   ├── tests/                      # 前端测试
│   └── vite.config.mts             # Vite 构建和开发代理配置
├── deploy/                        # 更新脚本与 systemd 服务模板
├── requirements.in                # Python 直接依赖
└── requirements.txt               # Python 依赖锁，供本地、CI 和部署安装
```

实际运行配置 `backend/.env`、`backend/system_config.json`，账号目录 `backend/user_data/`，以及生成的日志 `backend/log_config/` 和前端产物 `frontend/dist/` 由各环境自行维护。部署手册 `docs/DEPLOY.md` 和配置说明 `docs/CONFIG.md` 在本地工作区维护。

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

修改 Python 依赖时，编辑 `requirements.in`，再运行 `uv pip compile --universal --python-version 3.10 --no-annotate requirements.in -o requirements.txt` 生成锁文件。升级整个锁文件时加 `--upgrade`。部署根据 `requirements.txt` 的变化重新安装依赖。

首次运行需要构建一次：后端启动时会检查 `frontend/dist`，即使使用Vite开发也需要该目录。构建产物不随仓库提交。

首次安装时复制 `backend/system_config.example.json` 为 `backend/system_config.json`，在 `database` 里填写 MySQL 地址、用户名和库名。全局配置支持 `//` 行注释，字段用途、单位和可选值写在对应字段旁。编辑后重启后端生效。复制 `backend/.env.example` 为 `backend/.env` 后，填写 `MYSQL_PASSWORD` 和管理员初始密码。后端启动时会创建数据库和所需表。登录后可在网页设置中保存模型和 API Key。

已有部署升级时，保留原来的 `backend/system_config.json`、`backend/.env` 和 `backend/user_data/`。首次启动会给旧版纯 JSON 全局配置补齐默认项和字段说明。MySQL 密码会迁移到 `.env` 的 `MYSQL_PASSWORD`，并从旧全局配置移除。已经设置的 `MYSQL_PASSWORD` 优先。如果全局配置尚未创建，旧 `.env` 中的并发参数仍可迁入。

如果服务器已有自定义的 `backend/settings.json`，更新前请保留一份本地副本供迁移。这个文件由 Git 忽略，启动时仍可读取。旧管理员模型会迁入 `.env` 的 `ADMIN_PROVIDERS`，新安装从全局配置读取默认模型。

`auth` 管理会话有效期、密码长度和认证限流，`profile` 管理昵称长度，`defaults` 设定新账号的初始主题、快捷键和头像。`chat` 管理并发、消息长度、历史压缩和启动时的历史清理。`model_runtime` 管理模型连接验证并发，`logging` 管理日志轮转和启动清理。

## 聊天设置

管理员和普通用户都可以在网页设置中查看和更改厂商的 API 基础地址。模型选项来自 `system_config.json`，后端用同一份列表校验测试和保存请求。自定义地址须兼容 OpenAI 聊天接口，并包含所需的 `/v1` 等基础路径。普通用户的自定义地址还须使用公网 HTTPS。清空地址并保存可恢复全局配置中的厂商地址和协议。

管理员可以使用厂商默认地址，以及服务器在 `models.providers.<厂商>.admin_allowed_base_urls` 中批准的完整地址。修改批准列表后重启后端。已有管理员自定义地址也须先经批准，才能在启动时加载。管理员更换地址时须为目标地址填写 API Key。

“测试连接”只验证连接，不保存或切换；“保存并切换”验证成功后保存配置，并更新聊天、分析、摘要三个代理。验证或保存失败时保留当前模型。

输入习惯支持多行输入与 Enter / Ctrl+Enter 发送；Shift+Enter 始终换行，中文输入法确认文字不会触发发送。每条消息最多 8000 个字符。

管理员在 `backend/.env` 的 `ADMIN_PROVIDERS` 中按厂商各保存一条 `base_url`、`model_name` 和 `api_key`。普通用户的 `backend/user_data/<账号目录名>/model.json` 也按厂商保存这三个字段。后端使用对应账号的厂商配置，同厂商切换模型会共用地址和密钥，密钥留空时沿用已保存的值。旧普通用户文件在首次读取时迁移。真实配置文件由 Git 忽略。

在“设置 → 偏好”中切换主题或发送快捷键后，系统自动把两项都保存到当前账号的 `backend/user_data/<账号目录名>/preferences.json`。刷新、换浏览器和重启后都能恢复已保存的偏好。旧偏好文件缺少主题字段时使用默认白。

回复生成与显示沿用原有逻辑：自然分条，显示间隔按 `min(1000 + 100 × 当前回复长度, 5000)` 毫秒计算，最后一条回复后不等待。

侧边栏中的聊天回顾搜索当前页面已显示的对话，并支持点击定位原消息；不另外保存聊天记录，沿用原有启动清理和摘要压缩规则。

`POST /history` 的 `num` 接受 `-10000` 到 `10000` 的整数或 `null`。`front: true` 的基础顺序是最早到最新，`front: false` 则相反。正数按基础顺序读取，负数反向读取 `abs(num)` 条。例如 `{"num": -3, "front": true}` 从最新一轮开始读取三条。`0` 或 `null` 取最新的至多 10000 条，再按 `front` 指定的顺序返回。响应中的 `num` 原样回显请求参数，实际返回条数以 `history_messages` 数组长度为准。

聊天时模型最多使用每账号最近 256 轮原文和更早的摘要。超过 `chat.history_compression_tokens` 或仍有更早原文时，系统从数据库按轮次读取最早的记录，每次最多压缩 32 轮。摘要与对应原文的删除在同一事务内提交。

检查命令为 `python -m unittest discover -s backend/tests -v`、`npm --prefix frontend test`、`npm --prefix frontend run typecheck` 和 `npm --prefix frontend run build`。CI 使用独立的 MySQL 8.4 容器运行数据库集成测试。本地运行数据库用例时，将 `XIAOWAN_TEST_MYSQL_PORT` 和 `XIAOWAN_TEST_MYSQL_PASSWORD` 指向 `127.0.0.1` 上的独立测试实例及其 `root` 密码。未配置端口时本地跳过这组用例。

## 账户和启动

打开登录页可用用户名和密码注册普通账号。用户名支持 3–32 个英文字母、数字或下划线，唯一性检查不区分大小写，`admin` 及其大小写变体为保留用户名。注册和设置新密码时使用 6–15 个字符。账户和密码哈希保存在 MySQL。

首次安装或使用空数据库时，在 `backend/.env` 的 `ADMIN_INITIAL_PASSWORD` 填写自己设置的 6–18 字符密码，然后正常启动后端。服务会创建用户名为 `admin` 的账号，并在 MySQL 中保存加盐哈希。确认登录成功后可从 `.env` 移除这一项。已有管理员的部署沿用 MySQL 中的账号和密码，改密在网页的个人资料中完成。

如果旧部署仍有自定义的 `backend/auth_config/profile.json`，升级前可保留本地副本。服务启动时会把它导入尚未创建的管理员资料。

登录 Cookie 默认有效期为 8 小时，可在 `auth.session_ttl_seconds` 修改。刷新页面可恢复登录，后端重启后需重新登录。退出只撤销当前登录，改密码后该账号的所有会话都会失效。同一浏览器配置下的标签页共享 Cookie。

菜单的“个人资料”可以修改昵称和选择六张系统头像，默认使用小猫。注册时创建 `backend/user_data/<用户名>/profile.json`，目录名保留注册时的大小写。纯数字用户名和 Windows 保留设备名的目录加 `~` 前缀，例如 `~123`。后端启动时会把已有账号的数字目录整体改为用户名目录。目标目录已存在时会记录冲突并保留原来的数字目录。头像素材位于 `frontend/public/avatars/system/`。

已认证账号的运行日志写入 `backend/user_data/<账号目录名>/logs/app.log`，以 `[账号 admin]` 这样的用户名标签记录请求结果、每轮聊天的节点执行、回复条数、Token 和耗时。启动、匿名请求、认证审计、全局模型切换和数据库连接池事件写入 `backend/log_config/app.log`。日志使用 UTC 时间，访问记录使用路由模板并省略查询参数。系统级告警和错误也写到标准错误，通过 systemd 启动时可在 `journalctl` 查阅。默认每个文件约 2 MB 时轮转，最多保留三份归档，重启时保留归档。

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

> 默认启动时清空历史对话、摘要和当前日志。需要保留时，可在 `backend/system_config.json` 中分别关闭 `chat.clear_history_on_startup` 和 `logging.clear_on_startup`。

后端使用单个 Uvicorn worker。每个账号首次发消息时创建自己的 LangGraph 和记忆管理器，不同账号可并行，同一账号逐轮处理。`system_config.json` 的 `chat.concurrency`、`chat.queue_size` 和 `chat.queue_timeout` 控制最多同时执行数、等待名额和排队秒数，默认分别为 4、8、2。排队失败时可以从原消息重试。

使用Vite开发时，前端修改会自动刷新；访问 `8000` 时，修改前端后需重新执行 `npm --prefix frontend run build`（在项目根目录）。修改后端后需重启后端服务。

## 升级已有账号的独立目录

升级已有服务器时，一并备份 MySQL、`backend/user_data/` 和服务端配置。保留现有账号目录的名称。

升级后正常启动后端。新注册账号会自动创建个人文件。已有账号首次读取缺失的偏好文件时，后端会从 MySQL 导入旧快捷键。已有个人文件优先使用。
