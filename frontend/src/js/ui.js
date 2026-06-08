//所有DOM操作

export const chatBox = document.getElementById("chatBox");//聊天记录框
export const sendBtn = document.querySelector(".sendbtn");//发送按钮
export const inputBox = document.getElementById("userInput");//文本输入框
export const comeToBottom = document.getElementById("comeToBottom");//回到底部按钮
export const sidebarBtn = document.querySelector('#sidebar-zone .sidebar-btn');//侧边栏唤起按钮
export const sidebar = document.querySelector('#sidebar-zone .sidebar');//侧边栏
export const menuBtn = document.getElementById('menuBtn');//菜单唤起按钮
export const menu = document.getElementById('menu');//菜单
export const menuSelect = document.querySelectorAll('#menu .value')//获得菜单里所有选项
export const themeInput = document.querySelectorAll('#menu .theme-select label input[name="themeRadio"]');//获得所有主题选择按钮
export const windowAlertContainer = document.querySelector('#alert-window-container');//提示弹窗
export const windowAlertP = document.querySelector('.alert-window .alert-window-content p');//提示弹窗内容


export function lockSendBtn() {//锁住发送按钮
    sendBtn.disabled = true;
    sendBtn.innerText = '......';
    var h = document.querySelector('#title h1');
    h.classList.add('loading');
}

export function unlockSendBtn() {//解锁发送按钮
    sendBtn.disabled = false;
    sendBtn.innerText = '发送';
    var h = document.querySelector('#title h1');
    h.classList.remove('loading');
}

export function scrollToBottom(smooth = true) {// 启用平滑滚动
    chatBox.scrollTo({
        top: chatBox.scrollHeight,
        behavior: smooth ? 'smooth' : 'auto'
    });
}

//根据人格更新UI,同时也是设置人格
export function updateUi(persona){
    if (!persona) return;

    //标签页标题
    if(persona.BOT_NAME){
        document.title = persona.BOT_NAME;
    }

    const welcome = document.querySelector('#title h1');
    welcome.innerText = persona.BOT_NAME;

    //助手头像（保存到全局，后面用）
    window.BOT_AVATAR = persona.BOT_AVATAR;

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
