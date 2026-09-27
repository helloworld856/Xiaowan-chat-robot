//聊天气泡创建
import {chatBox, scrollToBottom} from './ui.ts';
import {modelConfig} from './global_config.ts';
import type {ConversationItem} from './global_config.ts';

function createAvatar(src: string, alt: string, className: string) {
    const avatar = document.createElement('div');
    const image = document.createElement('img');
    avatar.className = className;
    image.src = src;
    image.alt = alt;
    avatar.appendChild(image);
    return avatar;
}

function createUserMessage(text: string) {
    const message = document.createElement('div');
    const bubble = document.createElement('div');
    const triangle = document.createElement('div');
    const avatar = createAvatar(window.USER_AVATAR, '你的头像', 'user_avatar');

    message.className = 'user_message';
    bubble.className = 'user_bubble';
    triangle.className = 'user_triangle';
    bubble.textContent = text;
    message.append(bubble, triangle, avatar);
    return message;
}

function createAssistantMessage(text: string) {
    const message = document.createElement('div');
    const bag = document.createElement('div');
    const bubble = document.createElement('div');
    const triangle = document.createElement('div');
    const avatar = createAvatar(window.BOT_AVATAR, '对方的头像', 'assistant_avatar');

    message.className = 'assistant_message';
    bag.className = 'assistant_bag';
    bubble.className = 'assistant_bubble';
    triangle.className = 'assistant_triangle';
    bubble.textContent = text;
    bag.append(avatar, triangle, bubble);
    message.appendChild(bag);
    return message;
}

export async function addUserMessage(text: string){
    console.log('用户消息:' + text);
    chatBox.appendChild(createUserMessage(text));
}

export async function addAssistantMessage(messages: string[]){
    const baseSendInterval = 1000;
    const shouldScrollToBottom = chatBox.scrollTop + chatBox.clientHeight >= chatBox.scrollHeight - 2;

    for (const [index, text] of messages.entries()) {
        chatBox.appendChild(createAssistantMessage(text));

        if (shouldScrollToBottom) scrollToBottom();

        const delay = Math.min(baseSendInterval + 100 * text.length, 5000);
        console.log('助手消息：', text);
        if (index !== messages.length - 1){
            console.log(`下一条助手消息于${delay}ms后发送`);
            await new Promise(resolve => setTimeout(resolve, delay));
        }
    }
}

//显示历史聊天记录
export function showChatHistory(chatHistory: ConversationItem[]){
    for (const item of chatHistory) {
        chatBox.appendChild(createUserMessage(item.user));
        for (const text of item.assistant) {
            chatBox.appendChild(createAssistantMessage(text));
        }
    }
    scrollToBottom();
}

//设置主题
export function switchTheme(theme:string){
    document.documentElement.className = theme;
    localStorage.setItem('theme', theme);
    console.log('主题已切换：', theme);
}

export function model_invalid(){
    if (!modelConfig.model_valid){
        window.alert('请配置有效的模型！');
        return false;
    }
    return true;
}
