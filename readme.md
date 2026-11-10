# 小晚AI角色对话软件

版本：`1.0.0`。小晚是一个供单人本地使用的AI聊天应用。前端使用TypeScript + Vite，后端使用FastAPI + LangGraph，通过外部API调用DeepSeek或通义千问。对话在本次后端运行期间保存到MySQL，历史过长时自动压缩为摘要。

## 主要功能

- 根据用户意图和情绪生成角色化回复
- 在页面中切换DeepSeek或通义模型
- 保存对话、加载本次运行的历史并自动生成历史摘要
- 支持三套前端主题；人设通过 `backend/persona_config/xiaowan.py` 修改
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
npm --prefix frontend install
npm --prefix frontend run build
```

首次运行需要构建一次：后端启动时会检查 `frontend/dist`，即使使用Vite开发也需要该目录。构建产物不随仓库提交。

复制 `backend/.env.example` 为 `backend/.env`，填写要使用的API Key和MySQL `root` 密码：

```env
DEEPSEEK_API_KEY=your-key
TONGYI_API_KEY=your-key
PASSWORD=your-mysql-password
```

只需填写实际使用的模型Key。数据库 `XiaoWan` 和所需表会自动创建，数据库账号、地址和端口在 `backend/config/cfg.py` 中配置。

模型弹窗中的API-key输入框目前未接入，请在 `.env` 中设置密钥并重启后端。示例中的 `DEFAULT_MODEL_*` 暂未使用，实际模型在页面设置中选择。

## 聊天设置

设置页支持模型厂商、模型名称与自定义 API 基础地址。名称可以手动输入；地址留空使用原有厂商接口，填写自定义地址时须兼容 OpenAI 聊天接口，包含所需的 `/v1` 等基础路径。仍使用所选厂商在 `.env` 中配置的密钥。

“测试连接”只验证连接，不保存或切换；“保存并切换”验证成功后保存配置，并更新聊天、分析、摘要三个代理。验证或保存失败时保留当前模型。

输入习惯支持多行输入与 Enter / Ctrl+Enter 发送；Shift+Enter 始终换行，中文输入法确认文字不会触发发送。

模型配置和输入习惯统一写入 `backend/settings.json`，刷新和重启后恢复。输入习惯使用“保存输入设置”保存；也可手动编辑 JSON 后在设置页点击“重新读取设置”。手动修改模型配置后需要刷新页面进行验证和切换。JSON 中不保存 API Key。模型和输入设置仍使用单人本地配置。

回复生成与显示沿用原有逻辑：自然分条，显示间隔按 `min(1000 + 100 × 当前回复长度, 5000)` 毫秒计算，最后一条回复后不等待。

侧边栏中的聊天回顾搜索当前页面已显示的对话，并支持点击定位原消息；不另外保存聊天记录，沿用原有启动清理和摘要压缩规则。

检查命令：`python -m unittest discover -s backend/tests -v`、`npm --prefix frontend test`、`npm --prefix frontend run typecheck`、`npm --prefix frontend run build`。测试不调用真实模型或业务数据库。

## 启动

当前只有一个管理员账户：用户名 `admin`，初始密码 `123456`，没有注册功能。密码以加盐哈希保存在 `backend/auth_config/admin.json`，不写入前端或普通设置文件。

打开页面后先登录，菜单中可退出登录。登录 Cookie 有效期为 8 小时，刷新页面会恢复有效登录；退出或重启后端后需要重新登录。登录失败连续达到 5 次时，同一客户端需等待 60 秒后再试。

菜单中的“个人资料”支持修改昵称和选择系统头像，登录用户名 `admin` 只读。系统提供小猫、狐狸、兔子、小熊、企鹅和青蛙六张头像，默认使用小猫，不提供上传功能。资料保存在 `backend/auth_config/profile.json`，刷新和重启后保留；保存后同步更新菜单昵称和聊天头像。

个人资料内可修改密码，需要输入当前密码并两次确认新密码（6–128 个字符）。成功后所有旧登录失效，使用新密码重新登录；保存失败时保留原密码和会话。头像素材位于 `frontend/public/avatars/system/`，制作提示词见 `docs/superpowers/avatars-prompts.md`。

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

访问 `http://localhost:5173`。Vite 会将 API 请求代理到 `127.0.0.1:8000`。

如果只想启动一个服务，先构建前端：

```bash
npm --prefix frontend run build
cd backend
python run_serve.py
```

然后访问 `http://127.0.0.1:8000`。首次打开后，在页面设置中选择模型。

> 当前设计会在每次启动后端时清空历史对话和历史摘要。

使用Vite开发时，前端修改会自动刷新；访问 `8000` 时，修改前端后需重新执行 `npm --prefix frontend run build`（在项目根目录）。修改后端后需重启后端服务。
