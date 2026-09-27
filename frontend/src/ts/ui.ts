import type { PersonaInfo } from './global_config.ts';

//使用as就是非空1断言，使用querySelector<HTMLButtonElement>(".sendbtn")依然有可能返回null，后面加上！就变成非空断言

//所有DOM操作
export const chatBox = document.getElementById("chatBox") as HTMLDivElement;//聊天记录框
export const sendBtn = document.querySelector<HTMLButtonElement>(".sendbtn")!;//发送按钮
export const inputBox = document.getElementById("userInput") as HTMLDivElement;//文本输入框
export const comeToBottom = document.getElementById("comeToBottom") as HTMLDivElement;//回到底部按钮
export const sidebarBtn = document.querySelector<HTMLButtonElement>('#sidebar-zone .sidebar-btn')!;//侧边栏唤起按钮
export const sidebar = document.querySelector<HTMLDivElement>('#sidebar-zone .sidebar')!;//侧边栏
export const menuBtn = document.getElementById('menuBtn') as HTMLDivElement;//菜单唤起按钮
export const menu = document.getElementById('menu') as HTMLDivElement;//菜单
export const menuSelect = document.querySelectorAll<HTMLButtonElement>('#menu .value')!;//获得菜单里所有选项
export const themeInput = document.querySelectorAll<HTMLInputElement>('#menu .theme-select label input[name="themeRadio"]')!;//获得所有主题选择按钮
export const windowAlertContainer = document.querySelector<HTMLDivElement>('#alert-window-container')!;//提示弹窗
export const windowAlertP = document.querySelector<HTMLParagraphElement>('.alert-window .alert-window-content p')!;//提示弹窗内容


export function lockSendBtn() {//锁住发送按钮
    sendBtn.disabled = true;
    sendBtn.innerText = '......';
    var h = document.querySelector('#title h1') as HTMLHeadingElement;
    h.classList.add('loading');
}

export function unlockSendBtn() {//解锁发送按钮
    sendBtn.disabled = false;
    sendBtn.innerText = '发送';
    var h = document.querySelector('#title h1') as HTMLHeadingElement;
    h.classList.remove('loading');
}

export function scrollToBottom(smooth = true) {// 启用平滑滚动
    chatBox.scrollTo({
        top: chatBox.scrollHeight,
        behavior: smooth ? 'smooth' : 'auto'
    });
}

// 打开时取消退出状态，便于快速关闭后再次打开。
export function openPopup(element: HTMLElement) {
    element.classList.remove('closing');
    element.inert = false;
    element.classList.add('open');
}

// 等 CSS 退出动画结束再隐藏；减少动态效果时会立即完成。
export async function closePopup(element: HTMLElement): Promise<boolean> {
    if (!element.classList.contains('open') || element.classList.contains('closing')) return false;
    element.classList.add('closing');
    element.inert = true;
    try {
        const animations = element.getAnimations();
        for (const child of element.children) {
            animations.push(...child.getAnimations());
        }
        await Promise.all(animations.map(animation => animation.finished));
    } catch {
        return false; // 重新打开时，原来的退出动画会被取消。
    }
    if (!element.classList.contains('closing')) return false;
    element.classList.remove('open', 'closing');
    element.inert = false;
    return true;
}

// 临时提示只放在页面里，不写入聊天记录。
export function showTypingIndicator() {
    hideTypingIndicator();
    const row = document.createElement('div');
    row.id = 'chat-typing';
    row.className = 'assistant_message';
    row.setAttribute('role', 'status');
    row.setAttribute('aria-label', '对方正在输入');

    const bag = document.createElement('div');
    bag.className = 'assistant_bag';
    const avatar = document.createElement('div');
    avatar.className = 'assistant_avatar';
    const img = document.createElement('img');
    img.src = window.BOT_AVATAR;
    img.alt = '';
    avatar.appendChild(img);
    const triangle = document.createElement('div');
    triangle.className = 'assistant_triangle';
    const bubble = document.createElement('div');
    bubble.className = 'assistant_bubble chat-typing-dots';
    bubble.setAttribute('aria-hidden', 'true');
    for (let i = 0; i < 3; i++) bubble.appendChild(document.createElement('span'));
    bag.append(avatar, triangle, bubble);
    row.appendChild(bag);
    chatBox.appendChild(row);
}

export function hideTypingIndicator() {
    document.getElementById('chat-typing')?.remove();
}

//根据人格更新UI,同时也是设置人格
export function updateUi(persona: PersonaInfo){
    if (!persona) return;

    //标签页标题
    if(persona.BOT_NAME){
        document.title = persona.BOT_NAME;
    }

    const welcome = document.querySelector('#title h1') as HTMLHeadingElement;
    welcome.innerText = persona.BOT_NAME;

    //助手头像（保存到全局，后面用）
    window.BOT_AVATAR= persona.BOT_AVATAR;

    //用户头像
    window.USER_AVATAR = persona.USER_AVATAR;
}


/*
 scrollTop = 150px（被卷上去的部分）
        ↓
┌─────────────────────────────────────────┐
│ ╭─────╮   这150px的内容在窗户上面         │
│ │消息1│   你看不到了                     │
│ ╰─────╯                                 │
│ ╭─────╮                                 │
│ │消息2│                                 │
│ ╰─────╯                                 │
├─────────────────────────────────────────┤ ← 窗户顶部
│ ╭─────╮                                 │
│ │消息3│                                 │
│ ╰─────╯     clientHeight = 300px        │
│ ╭─────╮     （窗户高度）                 │
│ │消息4│                                 │
│ ╰─────╯                                 │
│ ╭─────╮                                 │
│ │消息5│                                 │
│ ╰─────╯                                 │
├─────────────────────────────────────────┤ ← 窗户底部
│ ╭─────╮                                 │
│ │消息6│   这200px的内容在窗户下面         │
│ ╰─────╯   你也看不到了                   │
│                                         │
└─────────────────────────────────────────┘

scrollHeight = 150 + 300 + 200 = 650px（总高度）
*/
