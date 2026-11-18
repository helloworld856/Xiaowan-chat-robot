//聊天气泡创建
import {chatBox, scrollToBottom} from './ui.ts';
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
    avatar.title = window.USER_NAME || '你';

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

export function addUserMessage(text: string){
    const row = createUserMessage(text);
    chatBox.appendChild(row);
    return row;
}

export async function addAssistantMessage(messages: string[], isCurrent: () => boolean = () => true,
                                          onFirstReply?: () => void){
    const baseSendInterval = 1000;
    // 只在用户原本贴近底部时跟随回复，避免打断查看旧消息。
    const shouldScrollToBottom = chatBox.scrollTop + chatBox.clientHeight >= chatBox.scrollHeight - 2;

    for (const [index, text] of messages.entries()) {
        if (!isCurrent()) return;
        chatBox.appendChild(createAssistantMessage(text));
        if (index === 0) {
            console.log(`收到 ${messages.length} 条助手回复`);
            onFirstReply?.();
        }

        if (shouldScrollToBottom) scrollToBottom();

        const delay = Math.min(baseSendInterval + 100 * text.length, 5000);
        if (index !== messages.length - 1){
            console.log(`下一条助手消息将在 ${delay} ms 后显示`);
            await new Promise(resolve => setTimeout(resolve, delay));
        }
    }
}

//显示历史聊天记录
export function showChatHistory(chatHistory: ConversationItem[]){
    // 按已保存轮次补入缺少的历史，保留页面上还未入库的消息。
    const displayedRows = Array.from(chatBox.querySelectorAll<HTMLElement>('.user_message[data-conversation-round]'));
    const displayed = new Set(displayedRows.map(row => row.dataset.conversationRound));
    let nextDisplayed = 0;
    for (const item of chatHistory) {
        const round = String(item.conversation_round);
        if (displayed.has(round)) continue;
        let anchor = displayedRows[nextDisplayed];
        while (anchor && Number(anchor.dataset.conversationRound) < item.conversation_round) {
            anchor = displayedRows[++nextDisplayed];
        }
        const row = createUserMessage(item.user);
        row.dataset.conversationRound = round;
        displayed.add(round);
        const conversation = document.createDocumentFragment();
        conversation.append(row);
        for (const text of item.assistant) {
            conversation.append(createAssistantMessage(text));
        }
        chatBox.insertBefore(conversation, anchor ?? null);
    }
    scrollToBottom();
}

//设置主题
export function switchTheme(theme:string){
    document.documentElement.className = theme;
}
