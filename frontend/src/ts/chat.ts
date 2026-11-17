import {AuthRequiredError, CancelledRequestError, ChatNotStartedError, chatAPI, getRequestGeneration, historyAPI} from './api.ts';
import {
    chatBox, hideTypingIndicator, inputBox, lockSendBtn, scrollToBottom, sendBtn, showAlert, showTypingIndicator, unlockSendBtn
} from './ui.ts';
import {addAssistantMessage, addUserMessage, showChatHistory} from './utils.ts';

const maxMessageLength = 8000;

function showDeliveryFailure(row: HTMLDivElement, text: string, error: ChatNotStartedError) {
    const marker = document.createElement('button');
    marker.type = 'button';
    marker.className = 'chat-send-failed';
    marker.textContent = '!';
    marker.title = '发送失败';
    marker.setAttribute('aria-label', error.retryable ? '发送失败，重试发送' : '发送失败，查看原因');
    marker.addEventListener('click', async () => {
        if (!row.isConnected) return;
        if (!error.retryable) {
            showAlert(error.message);
            return;
        }
        if (sendBtn.disabled) return;
        marker.remove();
        chatBox.appendChild(row);
        await submitMessage(text, row);
    });
    row.prepend(marker);
    showAlert(error.message);
}

// 超时不代表服务端未保存，先刷新历史供用户核对。
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
        // 只为已保存的轮次标记编号，刷新历史时用它去重。
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
    // 先用字符串长度快速检查，超限时再按字符计数，与后端一致。
    if (text.length > maxMessageLength && [...text].length > maxMessageLength) {
        showAlert(`聊天消息最多 ${maxMessageLength} 个字符`);
        return;
    }

    // 等待期间可以继续输入下一条消息。重试会从气泡读取原文，不改变输入框。
    inputBox.value = '';
    await submitMessage(text, addUserMessage(text));
}
