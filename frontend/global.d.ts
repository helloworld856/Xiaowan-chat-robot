export {}; // 这行是为了让文件成为模块，避免污染全局

declare global {
  // 资料与人设加载后更新，创建气泡时读取。
  interface Window {
    BOT_AVATAR: string;
    USER_AVATAR: string;
    USER_NAME: string;
  }
}
