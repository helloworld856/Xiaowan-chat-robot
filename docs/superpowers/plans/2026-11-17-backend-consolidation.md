# 小晚后端结构整理与去重实施方案

> 执行时使用 Superpowers 的 executing-plans，按任务逐项实现和验收。本文件是待执行方案。

**目标** 统一后端包结构，删除重复的实现和职责过薄的文件，让账号、聊天、模型和存储代码各有明确的归属。

**设计** 以 `backend/app/` 为唯一业务包。先合并重复的文件写入与目录规则，再搬迁模块，最后归并模型配置和运行时状态。起点为 `codex/development` 的 `0c954c3`。

**技术栈** Python 3.10+、FastAPI、Uvicorn、LangGraph、MySQL 8 和 unittest。部署背景见[交接文档](../../HANDOFF.md)，本方案采用对话中确认的“目录整理与重复实现一起处理”。

## 全局约束

- 保留 `python backend/run_serve.py`、`cd backend` 后的 `python run_serve.py` 以及迁移脚本的 `--dry-run`、`--apply`、`--rollback-preferences`。
- 保留全部 HTTP 方法、URL、请求字段、响应字段和状态码。保持 Cookie 有效期 8 小时、同源写请求校验、旧标签页账号核对、`Cache-Control: no-store` 及凭据校验错误脱敏。
- 保留 `backend/.env`、`backend/settings.json`、`backend/user_data/<账号 ID>/`、`backend/log_config/app.log`、`backend/auth_config/{admin,profile}.json`、`backend/persona_config/persona.json`、`backend/utils/deepseek_tokenizer/tokenizer.json` 和 `frontend/dist/` 的现有位置。整理后部分旧目录只存数据或资源，名称保留以兼容现有部署。
- 保留 MySQL 表和账号 ID、注册事务、旧数据导入、历史摘要事务，以及启动清空聊天原文与摘要的现有行为。聊天继续使用单个 Uvicorn worker，默认同时处理 4 条，最多 8 条等待，排队超时 2 秒。
- 保留管理员从环境变量取密钥及配置自定义地址的权限。普通账号的模型、私有密钥、代理缓存、聊天记录和日志继续按服务端认证账号的 ID 隔离。前端、数据库结构和依赖版本保持现状。

## 最终目录

```text
backend/
├── run_serve.py
├── migrate_user_data.py
├── app/
│   ├── main.py                 # ASGI 对象和前端静态资源挂载
│   ├── application.py          # 应用装配、路由注册和生命周期
│   ├── config.py               # 软件元数据、运行参数和部署路径
│   ├── json_files.py            # 原子写入 JSON
│   ├── account_logging.py       # 系统日志、账号日志和上下文
│   ├── api/
│   │   ├── auth.py
│   │   ├── auth_middleware.py
│   │   ├── credential_route.py
│   │   ├── chat.py
│   │   ├── history.py
│   │   ├── model.py
│   │   ├── persona.py
│   │   ├── profile.py
│   │   ├── settings.py
│   │   └── system.py
│   ├── accounts/
│   │   ├── auth_service.py
│   │   ├── repository.py
│   │   ├── directory.py
│   │   ├── files.py
│   │   ├── preferences.py
│   │   └── profile.py
│   ├── chat/
│   │   ├── scheduler.py
│   │   ├── workflow.py
│   │   ├── nodes.py
│   │   ├── state.py
│   │   └── memory.py
│   ├── llm/
│   │   ├── factory.py
│   │   ├── configuration.py
│   │   ├── admin_settings.py
│   │   ├── personal_models.py
│   │   ├── persona.py
│   │   ├── output_parser.py
│   │   └── token_counter.py
│   └── database/
│       ├── mysql.py
│       └── pool.py
└── tests/
```

包内使用 `app.*` 或相对导入，`app/__init__.py` 与子包的 `__init__.py` 保持轻量。HTTP 路由依赖业务模块，业务模块通过注入的存储和模型服务工作。账号文件操作保持独立于日志、认证和模型创建。历史缓存与 SQL 暂时合在 `chat/memory.py`，本次没有必要再拆文件。

## 审查重点

1. `os.replace()` 失败时，原有 JSON 内容保留，临时文件清除。任务 1 验证。
2. 找不到旧用户偏好时，仍能补齐默认文件。并发保存的偏好优先于迟到的旧库读取结果。任务 2 验证。
3. 从仓库根目录或 `backend/` 运行原命令时，加载的是同一套模块和部署路径。任务 3 和任务 4 验证。
4. 测试连接超时、管理员保存失败、已有代理仍在使用时，模型状态与保存结果一致。任务 5 验证。
5. A、B 并行聊天和切换账号时，历史、摘要、模型、文件和日志各归所属账号。任务 6 验证。

## 任务 0 · 固定基线

- [ ] 在仓库根目录运行 `git status --short --branch` 和 `git log -1 --oneline`。确认实际起点并记录用户已有改动。
- [ ] 使用已安装后端依赖的 Python 运行 `python -m unittest discover -s backend/tests -v`。未配置隔离 MySQL 时清除当前终端的 `XIAOWAN_TEST_MYSQL_PORT`。Windows 可用 `D:\.conda\langgraph_env\python.exe` 代替 `python`。此前在基线版本实测 101 项，98 项通过，3 项隔离 MySQL 测试跳过。此次执行时重新记下结果。受限 Windows 沙箱如果卡在异步事件循环的套接字初始化，改用允许本机回环的终端，避免为了测试环境修改产品代码。
- [ ] 运行 `npm --prefix frontend test`。此前基线为 31 项通过。若业务 HTTP 合约有差异，用实测基线定位并记录。
- [ ] 记录现有 FastAPI 路由的路径、方法、请求及响应类型。重点核对 `/auth/*`、`/chat`、`/history`、`/profile`、`/persona`、`/settings`、四个 `/model*` 接口、`/version` 和 Chrome DevTools 探测路径。用测试实例获取路由，避免启动业务数据库。

## 任务 1 · 合并三套 JSON 原子写入

**改动文件** 新建 `backend/app/json_files.py`，修改 `backend/user_data_config.py`、`backend/settings_config.py`、`backend/auth_config/accounts.py`。

- [ ] 新建 `atomic_write_json(path: Path, value: object) -> None`。临时文件与目标位于同一目录，沿用 UTF-8、两空格缩进、保留非 ASCII 字符和末尾换行。写入后 flush、fsync、替换，最后清理临时文件。
- [ ] 将 `replace_json()` 的写入交给公共函数，保留它对父目录的检查。管理员设置仍自己创建父目录并只写 `model` 字段。本地账号存储继续按原格式写旧管理员凭据，并将 OSError 转成原有账号存储错误。
- [ ] 更新因移动写入实现而失效的测试替换目标。运行 `python -m unittest discover -s backend/tests -p "test_settings.py" -v` 和 `python -m unittest discover -s backend/tests -p "test_profile.py" -v`。现有失败注入应继续证明旧设置与资料完整，并且改密写盘失败时密码和会话保持原状。
- [ ] 检查生产代码中的 `NamedTemporaryFile`、`os.fsync` 和 `os.replace`。上述三类 JSON 文件只保留一套原子写入序列。完成后形成独立检查点。

## 任务 2 · 统一账号目录检查与补齐规则

**改动文件** `backend/user_directory_store.py`、`backend/migrate_user_data.py`、`backend/tests/test_user_directories.py`。后续任务将目录模块移到 `app/accounts/directory.py`。

- [ ] 在 `UserDirectoryStore` 中统一定义每种账号所需的文件和目录，由 `inspect_missing(user: User) -> set[Path]` 和现有 `ensure(user, preferences=None)` 共用。全部账号需要 `profile.json`、`preferences.json` 和 `logs/`，只有普通账号需要 `model.json`。
- [ ] 迁移脚本将 `missing_files()` 的职责交给 `inspect_missing()`。检查使用现有的无副作用读方法。文件或目录不存在时报告缺失，格式错误、路径类型错误和权限错误继续进入异常报告。保留旧资料导入和偏好回退的顺序。
- [ ] 保留各存储的缺失和损坏处理策略。注册时仍使用显式默认偏好，避免在注册事务内借另一条连接读旧表。旧偏好的查询仍在文件锁之外，写入前重新检查现有文件。
- [ ] 运行 `python -m unittest discover -s backend/tests -p "test_user_directories.py" -v`。检查预检只读、可重复迁移、损坏文件保留和并发保存优先。若现有用例没有覆盖管理员和普通账号的预检清单差异，在现有测试文件补一个双角色用例，断言仅普通账号的缺失集合包含 `model.json`。

## 任务 3 · 统一导入、入口和 HTTP 文件

**改动文件** `backend/app/main.py`、`backend/app/core/{app,auth,config}.py`、`backend/app/routers/`、`backend/app/schemas/`、`backend/app/static/mount.py`、`backend/run_serve.py` 和受影响测试。

- [ ] 将原 `core/app.py` 的 `create_app(...)` 和 `main.py` 的 `build_application(db, profile_store, legacy_root)` 移到 `app/application.py`。保留前者供认证与资料测试注入依赖，保留后者组装生产服务和集成测试实例。测试从 `app.application` 导入工厂，无需构建前端或挂载静态文件才可导入。
- [ ] `main.py` 只保留生产 ASGI 实例和静态挂载。将 `NoCacheStaticFiles` 随挂载放到 `main.py`，保留响应的缓存控制和路由注册先于根静态挂载的顺序。
- [ ] 将 `app/routers/` 的业务路由移到 `app/api/`。把 `misc.py` 与 `version.py` 合并进 `api/system.py`。把 `CredentialRoute` 移到 `api/credential_route.py`，供认证和模型路由共用。将 `core/auth.py` 移到 `api/auth_middleware.py`。
- [ ] 将聊天、历史和模型的请求与响应类型移到各自路由模块。保留类名和字段定义，包括目前用于维持 OpenAPI 类型名的 `ModelConfigRequest`。移除原 `routers/`、`schemas/` 和 `static/mount.py` 的 Python 文件。
- [ ] 将旧 `config/cfg.py` 与 `app/core/config.py` 的参数合到 `app/config.py`，保留 `configer`、`SOFTWARE_NAME` 和 `VERSION`。版本日志由应用启动时写入，配置模块保持独立于日志模块，避免日志读取配置时循环导入。后端资源路径从 `app/config.py` 解析出的 `backend/` 根目录计算。删除应用内部对 `sys.path` 的修改。
- [ ] 保留两种目录下的启动方式以及 Uvicorn 的 `app.main:app` 目标。启动器先加载 `backend/.env`，再让 Uvicorn 导入 ASGI 对象。数据库密码、聊天容量和模型密钥仍在加载环境后读取。在 `backend/tests/_imports.py` 集中设置测试导入根为 `backend/`，替换各测试的重复路径配置，保留仓库根目录的 unittest 命令。测试使用包模块导入替代对已搬迁业务文件的路径加载，原有测试断言继续生效。
- [ ] 运行 `python -m unittest discover -s backend/tests -v`，对照任务 0 的路由清单和 OpenAPI 类型名。检查认证与模型路由的校验错误继续省略密码和 API Key 原值。

## 任务 4 · 归拢账号、聊天、日志和数据库代码

| 原位置 | 目标位置及操作 |
|---|---|
| `auth_config/__init__.py`、`auth_config/accounts.py` | 分别移到 `app/accounts/auth_service.py`、`app/accounts/repository.py`。保留测试使用的本地账号存储、SQL 实现与旧管理员资料导入。 |
| `user_data_config.py`、`user_directory_store.py`、`profile_config.py` | 分别移到 `app/accounts/files.py`、`directory.py`、`profile.py`。文件操作使用任务 1 的公共写入。 |
| `settings_config.py`、`personal_model_config.py` | 将 `Preferences`、`FilePreferencesStore`、旧偏好 SQL 存储移到 `accounts/preferences.py`；将管理员配置类型及读写移到 `llm/admin_settings.py`；个人模型移到 `llm/personal_models.py`；设置 API 的 `UserSettings` 移到 `api/settings.py`，保持 JSON 和类型名。继续读取旧版 `settings.json` 中用于迁移的偏好。 |
| `app/core/session.py`、`graph_config/{graph_build,nodes}.py`、`memory_config/memory.py` | 分别移到 `app/chat/{scheduler,workflow,nodes,memory}.py`，保留查询条件、事务和调度行为。 |
| `state_config/{state,__init__}.py` | 合并成 `app/chat/state.py`，其中定义 `AIChatState` 和 `create_initial_state(next_round: int = 0)`。 |
| `database_config/{init_mysql,pool}.py` | 移到 `app/database/{mysql,pool}.py`，建表、升级、连接池和启动清空 SQL 保持现状。 |
| `log_config/{log,account_context}.py` | 合并成 `app/account_logging.py`，保留日志位置、级别、账号上下文复制、轮转和文件权限。 |
| `persona_config/{init_persona,tool_personas,__init__}.py` | 合并为 `app/llm/persona.py`，沿用现有角色配置路径和两个工具提示词，保留人设缺失时的回退行为。 |
| `utils/{json_parser,tokens_count}.py` | 分别移到 `app/llm/{output_parser,token_counter}.py`，沿用原 Tokenizer 资源和缓存。 |

- [ ] 按表分批移动并同步入口、测试和字符串形式的 patch 目标。各组移动后运行对应测试。移除旧 Python 实现和纯转发导出文件，保留既有资源、配置与运行目录。模型工厂和全局代理迁移留给任务 5。
- [ ] 运行 `python -m unittest discover -s backend/tests -v`。核对 `test_startup_config.py` 的启动、日志和本地分词断言，以及聊天状态、两账号相同轮次和摘要事务用例。
- [ ] 检查仓库中仍引用旧模块名的生产及测试导入。更新 [README](../../../readme.md) 和[交接文档](../../HANDOFF.md)中的代码位置。启动、运行配置、数据和日志路径按全局约束核对。

## 任务 5 · 合并模型配置及运行时状态

**改动文件** `backend/agent_config/`、`app/llm/{configuration,factory,admin_settings,personal_models}.py`、`app/api/{model,settings}.py`、`app/chat/scheduler.py`、`app/application.py` 和模型相关测试。此处缩写的 `app/` 路径均位于 `backend/`。

- [ ] 把厂商客户端及三个代理的创建移到 `app/llm/factory.py`。提取供管理员和个人模型共用的厂商与模型名字段定义，两种输入继续分别声明 `base_url` 和 `api_key` 的约束。普通账号的请求模型继续拒绝额外字段。
- [ ] 在 `configuration.py` 提供 `ModelConfigurationService.configure_admin(model: ModelSettings, *, activate: bool) -> ModelConfigResponse` 和 `configure_personal(user_id: int, model: PersonalModel, *, activate: bool) -> ModelConfigResponse`。把 `ModelConfigResponse` 从路由移到该模块，保留原类名与 `status: bool`、`info: str`。两条路径复用同一个客户端构造、连接探测和保存前代理创建流程。管理员和普通账号各自解析密钥、目标地址和写入策略，四条 HTTP 路由仍分别验证角色。
- [ ] 在同一模块定义 `AdminModelRuntime.snapshot()`，在锁内返回当前管理员配置和完整代理三元组，未激活时返回 `None`。启动通过 `ModelConfigurationService.activate_saved_model(model: ModelSettings | None) -> None` 加载管理员配置。启动加载、模型切换、设置读取和聊天调度使用同一个应用内的运行时实例。设置读取在运行时未就绪时继续回退到文件中的模型名，标记为未就绪。删除旧模块的分散全局变量以及各调用者重复拼装代理的代码。个人代理缓存继续以 `user.id` 为键，独立于管理员状态。
- [ ] 保留测试连接只探测的语义和现有 2 个验证名额、20 秒验证等待、25 秒管理员切换预算。管理员在当前同步边界内先成功写文件，再发布新代理。验证或保存失败时保留旧配置和代理。一次聊天持有拿到的代理快照，后续聊天读取新快照。个人配置文件的原子写入成功后才更新该账号的代理缓存。
- [ ] 将 `test_settings_api.py` 的替身改为注入实际使用的存储、模型工厂和运行时实例，沿用原有测试断言。运行 `python -m unittest discover -s backend/tests -p "test_settings_api.py" -v`、`python -m unittest discover -s backend/tests -p "test_model*.py" -v` 和后端全量测试。重点验证探测超时后无迟到切换、失败时旧模型继续可用、全局模型不会影响普通账号、接口继续脱敏。

## 任务 6 · 最终验收与回退准备

- [ ] 在仓库根目录运行 `python -m unittest discover -s backend/tests -v`、`npm --prefix frontend test`、`npm --prefix frontend run typecheck` 和 `npm --prefix frontend run build`。四条命令均应退出 0。记录后端测试总数及跳过项。
- [ ] 在隔离 MySQL 8 实例上设置 `XIAOWAN_TEST_MYSQL_PORT` 和该实例需要的 `XIAOWAN_TEST_MYSQL_PASSWORD`，运行 `python -m unittest discover -s backend/tests -p "test_multi_user_mysql.py" -v`。应执行三项且无跳过。套件在本机随机命名的测试库中验证旧版迁移、跨账号并发、重启清理和持久数据。若环境缺少隔离实例，交付时明确记录这项未验证。
- [ ] 使用隔离配置分别验证两个启动路径。以 `curl -i --max-time 5 http://127.0.0.1:8000/auth/session` 检查未登录响应，应为 HTTP 200 和 `{"user":null}`。运行 `python backend/migrate_user_data.py --help` 验证迁移入口和参数，实际预检与回退只在测试库和临时用户目录中执行。
- [ ] 使用两个账号验证各自看到自己的资料、模型和历史。利用现有旧标签页测试核对 A 的账号头携带 B 的 Cookie 时返回 401。核对每轮请求的日志写入所属账号目录，普通账号无法调用管理员模型接口，设置响应中没有私有 API Key。
- [ ] 审查最终 diff。Python 业务实现集中在 `backend/app/`，原子写 JSON 只有一个实现，账号目录规则只有一处，管理员与个人模型共用验证步骤。前端源码、依赖清单、数据库结构和线上数据文件没有包含额外改动。
- [ ] 实施后以每项任务为独立提交检查点。发布前按现有交接约定备份 MySQL、个人目录和服务端配置。若部署后需要回退，退回已验证的上一版本并沿用原命令重启。此重构没有数据迁移步骤，重启仍会让会话失效并清空聊天原文与摘要。
