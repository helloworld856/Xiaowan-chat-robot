export {}; // 这行是为了让文件成为模块，避免污染全局

declare global {
  interface Window {
    BOT_AVATAR: string;
    USER_AVATAR: string;
  }
}