import {AuthRequiredError, CancelledRequestError, ChatNotStartedError, chatAPI, getRequestGeneration, historyAPI} from './api.ts';
import {
    chatBox, hideTypingIndicator, inputBox, lockSendBtn, scrollToBottom, sendBtn, showAlert, showTypingIndicator, unlockSendBtn
} from './ui.ts';
import {addAssistantMessage, addUserMessage, showChatHistory} from './utils.ts';

const maxMessageLength = 8000;

function showDeliveryFailure(row: HTMLDivElement, text: string, error: ChatNotStartedError) {
    const notice = document.createElement('div');
    notice.className = 'chat-delivery';
    const status = document.createElement('span');
    status.setAttribute('role', 'status');
    status.textContent = error.message;
    notice.append(status);
    if (error.retryable) {
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.textContent = '重试';
        retry.addEventListener('click', async () => {
            if (sendBtn.disabled || !row.isConnected) return;
            notice.remove();
            chatBox.appendChild(row);
            await submitMessage(text, row);
        });
        notice.append(retry);
    }
    row.prepend(notice);
}

async function checkUncertainDelivery(row: HTMLDivElement, text: string, isCurrent: () => boolean) {
    row.remove();
    let message = '已刷新聊天记录，此消息的发送结果仍需确认。请核对后再发送。';
    try {
        const history = await historyAPI(0);
        if (isCurrent()) showChatHistory(history.history_messages);
    } catch {
        message = '暂时无法核对这条消息，请查看聊天记录后再发送。';
    }
    if (!isCurrent()) return;

    const notice = document.createElement('details');
    notice.className = 'chat-delivery chat-delivery-uncertain';
    const summary = document.createElement('summary');
    const status = document.createElement('span');
    status.setAttribute('role', 'status');
    status.textContent = message;
    summary.append(status);
    const original = document.createElement('p');
    original.textContent = text;
    notice.append(summary, original);
    chatBox.appendChild(notice);
    scrollToBottom();
}

async function submitMessage(text: string, row: HTMLDivElement) {
    const generation = getRequestGeneration();
    const isCurrent = () => generation === getRequestGeneration();
    const startedAt = performance.now();
    lockSendBtn();
    showTypingIndicator();
    requestAnimationFrame(() => { if (isCurrent()) scrollToBottom(); });

    try {
        let data;
        try {
            data = await chatAPI(text);
        } catch (error) {
            if (!isCurrent() || error instanceof AuthRequiredError || error instanceof CancelledRequestError) return;
            hideTypingIndicator();
            if (error instanceof ChatNotStartedError) showDeliveryFailure(row, text, error);
            else await checkUncertainDelivery(row, text, isCurrent);
            return;
        }
        if (!isCurrent()) return;
        hideTypingIndicator();
        if (data.saved && data.conversation_round !== null) row.dataset.conversationRound = String(data.conversation_round);
        await addAssistantMessage(data.response, isCurrent, () => {
            const metric = 'xiaowan.chat.first-reply';
            performance.clearMeasures(metric);
            performance.measure(metric, {start: startedAt, end: performance.now()});
        });
        if (isCurrent() && !data.saved) showAlert('本轮对话未保存。回复仍可查看，请检查数据库连接。');
    } finally {
        if (isCurrent()) {
            hideTypingIndicator();
            unlockSendBtn();
        }
    }
}

export async function sendMessage() {
    if (sendBtn.disabled) return;
    const text = inputBox.value.trim();
    if (!text) return;
    if (text.length > maxMessageLength && [...text].length > maxMessageLength) {
        showAlert(`聊天消息最多 ${maxMessageLength} 个字符`);
        return;
    }

    // 等待期间可以继续输入下一条消息。重试会从气泡读取原文，不改变输入框。
    inputBox.value = '';
    await submitMessage(text, addUserMessage(text));
}
