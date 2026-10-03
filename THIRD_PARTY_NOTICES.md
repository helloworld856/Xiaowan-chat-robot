# 第三方来源与权属记录

项目：小晚 AI 角色对话软件；版本：1.0.0；整理日期：2026-10-03。

## 项目自身署名

根目录 `LICENSE` 的现有声明为 `Copyright (c) 2026 helloworld`，采用 MIT 许可，前端包元数据已统一为 MIT。本次保留现有署名，不据此认定 `helloworld` 与某个自然人或机构的身份关系。

登记前需由项目负责人核实：申请人的真实身份及其与该署名的对应关系；是否存在学校、单位、委托或合作开发约定；借用代码、素材及 AI 辅助开发内容的实际来源。可留存原始文件、开发记录、Git 历史、授权文件和相关协议。无法仅凭本仓库确认的权属信息，不应填写为已核实。

## 直接依赖

下表依据项目的 `requirements.txt`、`frontend/package-lock.json` 与上游许可证整理。Python 依赖使用版本范围，实际安装版本及其传递依赖需要随最终发布环境核对。此记录不是全部许可证原文的替代品。

| 组件 | 用途 | 上游许可及官方来源 |
| --- | --- | --- |
| FastAPI | Web API | [MIT](https://github.com/fastapi/fastapi/blob/master/LICENSE) |
| Uvicorn | ASGI 服务 | [BSD-3-Clause](https://github.com/Kludex/uvicorn/blob/main/LICENSE.md) |
| LangChain | 模型代理 | [MIT](https://github.com/langchain-ai/langchain/blob/master/LICENSE) |
| langchain-openai | DeepSeek 的兼容接口调用 | [MIT](https://github.com/langchain-ai/langchain/blob/master/libs/partners/openai/LICENSE) |
| langchain-community | 通义千问适配 | [MIT](https://github.com/langchain-ai/langchain-community/blob/main/LICENSE) |
| LangGraph | 分析、生成、记录工作流 | [MIT](https://github.com/langchain-ai/langgraph/blob/main/LICENSE) |
| Pydantic | 请求与响应数据模型 | [MIT](https://github.com/pydantic/pydantic/blob/main/LICENSE) |
| python-dotenv | 加载环境变量 | [BSD-3-Clause](https://github.com/theskumar/python-dotenv/blob/main/LICENSE) |
| PyMySQL | MySQL 访问 | [MIT](https://github.com/PyMySQL/PyMySQL/blob/main/LICENSE) |
| Transformers | 本地 tokenizer 加载与计数 | [Apache-2.0](https://github.com/huggingface/transformers/blob/main/LICENSE) |
| TypeScript | 前端类型检查 | [Apache-2.0](https://github.com/microsoft/TypeScript/blob/main/LICENSE.txt)，本地为 `frontend/node_modules/typescript/LICENSE.txt` |
| Vite | 前端开发与构建 | [MIT](https://github.com/vitejs/vite/blob/main/LICENSE)，本地为 `frontend/node_modules/vite/LICENSE.md`；其文件还列有捆绑依赖许可 |

MySQL 是外部运行环境，模型能力由 DeepSeek 或通义千问服务提供，均不作为本项目自有源代码。服务使用条件应查看对应供应商的官方协议。

## 随仓库提供、来源待核实的文件

| 文件或内容 | 已知情况 | 需要补充的证据 |
| --- | --- | --- |
| `backend/utils/deepseek_tokenizer/` 下的三个 JSON 文件 | 用于本地 token 计数；目录名与配置不足以证明具体上游模型和许可 | 原始仓库或下载链接、模型版本或提交号、该版本的 tokenizer 许可 |
| `frontend/public/avatars/xiaowan.png` | 聊天角色头像 | 创作记录或原始来源及授权 |
| `frontend/public/avatars/user_avatar.png` | 用户头像 | 创作记录或原始来源及授权 |
| `frontend/public/ico/favicon.ico` | 网站图标 | 创作记录或原始来源及授权 |
| `frontend/public/ico/arrow.png` | 聊天区域“回到底部”按钮使用的箭头图片 | 原始来源及授权 |
| `frontend/index.html` 中的 SVG 路径 | 菜单、设置、个人资料等图标 | 原始图标库及许可证，或自制图标说明 |
| `docs/images/` | 本次从实际前端界面截取；对话和接口返回使用隔离演示数据 | 用于操作说明；不作为真实模型效果或生产数据库验证证据 |

## 整理登记材料时

统一使用“小晚 AI 角色对话软件 V1.0.0”，从同一个确认后的代码版本整理源程序、说明书及截图。源程序范围以项目自有的 Python、TypeScript、HTML、CSS 为主，排除 `node_modules`、虚拟环境、编译产物、第三方 tokenizer 数据、密钥、数据库导出和日志；测试文件另行保留用于验证。

源程序与说明文档的页数、版式和交存方式按中国版权保护中心当前要求处理，并参照[《计算机软件著作权登记办法》](https://www.ncac.gov.cn/xxfb/flfg/bmgz/202410/t20241015_869486.html)；不要通过复制第三方源码或填充空行凑页数。申请人、开发完成日期、首次发表信息及开发方式须按真实情况填写。本文件中的待核实项目完成前，权属资料仍未定稿。
