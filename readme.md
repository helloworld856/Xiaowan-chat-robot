# 小晚 —— 一个有温度的 AI 聊天机器人

## 环境要求
* Python >= 3.10
* MySQL >= 8.0.45
* DeepSeek 或通义千问的 API Key

## 快速开始
1. 安装依赖：`pip install -r requirements.txt`
2. 复制 `backend/.env.example` 为 `backend/.env`，填入 API Key 和数据库密码
3. 启动服务：`cd backend && python run_serve.py`
4. 浏览器访问 `http://127.0.0.1:8000`，在设置中配置模型即可开始聊天

## 工作流
```
用户输入 → analysis（分析意图/情绪） → generate（生成回复/独白/情绪/动作） → 记录对话
```
长时间对话超出 token 上限时，自动将旧对话压缩为摘要，保持记忆连贯。

## API 接口
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/chat` | 发送消息，获取 AI 回复 |
| POST | `/history` | 获取历史对话 |
| POST | `/model` | 配置 / 切换模型 |
| GET | `/persona` | 获取人设信息 |
| GET | `/version` | 获取版本号 |

## 项目结构
```
├── frontend/            # 前端页面
├── backend/
│   ├── app/             # FastAPI 路由 & 核心
│   ├── agent_config/    # LLM 代理工厂 & 动态切换
│   ├── graph_config/    # LangGraph 工作流
│   ├── memory_config/   # 对话记忆（MySQL + 自动压缩）
│   ├── persona_config/  # 人设定义
│   └── config/          # 全局配置
├── requirements.txt
└── api接口文档.md
```
