# 固定管理员登录实现计划

> **For agentic workers:** Use superpowers:executing-plans and superpowers:test-driven-development; request an independent review before completion.

**Goal:** 在 codex/development 分支加入唯一 admin 账户（初始密码由用户指定），提供登录、退出、刷新恢复登录；没有注册入口或注册 API。
**Architecture:** 独立 JSON 保存加盐密码哈希，后端内存保存随机会话令牌的摘要，8 小时有效。HttpOnly/SameSite Cookie 随同源请求发送。默认保护所有业务 API，仅开放登录页静态资源与账户接口；跨来源写请求拒绝。页面先检查登录，再加载人设、模型、设置和聊天。
**Tech Stack:** 现有 FastAPI、Pydantic、TypeScript；Python 标准库 PBKDF2-HMAC-SHA256，不新增依赖。
**Spec:** 当前用户请求：只有 admin，密码 123456，暂不提供注册。

## Constraints
- 保留现有聊天、按长度显示消息、历史清理、模型配置和输入习惯。
- API Key 功能不在本次接入范围内。
- 不改 main，不创建注册/找回密码等新功能，不自动推送。
- 登录会话在后端重启后失效；刷新页面可以恢复有效会话。

## Review Focus
- 未登录不能绕过页面直接调用业务 API；错误密码与伪造 Cookie 拒绝。
- 退出和过期使服务端令牌失效；重新登录轮换 Cookie。
- 开发代理保持同源 Host；跨站写请求被拒绝。
- 退出时清空界面，旧请求或逐条显示的后续消息不能出现在登录页/新会话。
- 不持久化明文密码或前端 Token，JSON 配置和用户选择保持原样。

## Tasks
- [x] 后端：先写账户与 ASGI 请求行为测试并确认失败；实现哈希、会话、登录路由、访问保护与限流。
- [x] 前端：先写请求与登录失效测试；实现登录页、退出、登录恢复、请求过期隔离。
- [x] 验证：前后端完整测试、类型检查、构建；独立审查；更新使用说明。

验证记录：21 个后端测试、10 个前端测试、TypeScript 类型检查、Vite 构建及 Python 编译检查通过；真实浏览器在后端托管与 Vite 开发代理两种路径下完成错误密码、登录、刷新恢复、聊天、设置保存、退出验证。使用隔离模型/数据，不连接业务 MySQL 或真实模型。独立审查发现的代理 Host 冲突与退出/重新登录竞态均修复并复核通过。
