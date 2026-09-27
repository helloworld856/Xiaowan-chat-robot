# 小晚

小晚是一个本地运行的 AI 聊天应用。前端使用 TypeScript + Vite，后端使用 FastAPI + LangGraph，支持 DeepSeek 和通义千问。对话会保存到 MySQL，历史过长时自动压缩为摘要。

## 主要功能

- 根据用户意图和情绪生成角色化回复
- 在页面中切换 DeepSeek 或通义模型
- 保存对话、加载历史并自动压缩长期记忆
- 支持多套前端主题和自定义人设

## 环境要求

- Python 3.10+
- Node.js 和 npm
- MySQL 8+
- DeepSeek 或通义千问 API Key

## 安装与配置

```bash
python -m pip install -r requirements.txt
npm --prefix frontend install
```

复制 `backend/.env.example` 为 `backend/.env`，填写要使用的 API Key 和 MySQL `root` 密码：

```env
DEEPSEEK_API_KEY=your-key
TONGYI_API_KEY=your-key
PASSWORD=your-mysql-password
```

只需填写实际使用的模型 Key。数据库 `XiaoWan` 和所需表会自动创建。

## 启动

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
