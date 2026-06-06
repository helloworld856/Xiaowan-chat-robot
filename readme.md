# 这是一个名为"小晚"的聊天机器人

## 环境要求
* 建议 python >= 3.10
* MySQL >= 8.0.45
* 需要 DeepSeek 或通义千问的 API Key

## 快速开始
1. 安装依赖：`pip install -r requirements.txt`
2. 复制 `backend/.env.example` 为 `backend/.env`，填入 API Key 和数据库密码
3. 启动后端：`cd backend && python run_serve.py`
4. 浏览器访问 `http://127.0.0.1:8000`
