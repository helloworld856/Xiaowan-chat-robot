// Vite 构建和开发代理配置
import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
    plugins: [vue()],
    base: '/',           // 根据后端挂载路径调整
    build: {
        outDir: 'dist',    // 输出目录，需与后端托管目录对应
        emptyOutDir: true, // 每次构建清空旧文件
    },
    // 浏览器始终请求当前来源，开发时由 Vite 把 API 转发给 FastAPI。
    server: {
        // 显式保留浏览器 Host，使后端同源写请求校验也适用于开发代理。
        proxy: Object.fromEntries(['/chat', '/model', '/history', '/persona', '/version', '/settings', '/auth', '/profile']
            .map(path => [path, {target: 'http://127.0.0.1:8000', changeOrigin: false}]))
    }
});
