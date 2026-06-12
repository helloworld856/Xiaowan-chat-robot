// vite.config.ts，自定义vite配置
import { defineConfig } from 'vite';

export default defineConfig({
    base: '/',           // 根据后端挂载路径调整
    build: {
        outDir: 'dist',    // 输出目录，需与后端托管目录对应
        emptyOutDir: true, // 每次构建清空旧文件
    },
    // 后端运行在 127.0.0.1:8000，Vite dev server 默认在 localhost:5173。前端 api.ts 用 window.location.origin
    // 发请求，开发时所有 API 调用会打到 :5173 而不是 :8000。开发环境需要代理：

    server: {
        proxy: { '/chat': 'http://127.0.0.1:8000',
            '/model': 'http://127.0.0.1:8000',
            '/history': 'http://127.0.0.1:8000',
            '/persona': 'http://127.0.0.1:8000',
            '/version': 'http://127.0.0.1:8000'
        }
    }
});