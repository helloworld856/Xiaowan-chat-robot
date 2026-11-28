import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {compileModule, mountComponent} from './vue-harness.mjs';

const chatFormat = compileModule(fs.readFileSync(new URL('../src/ts/chat_format.ts', import.meta.url), 'utf8'));
const prefs = compileModule(fs.readFileSync(new URL('../src/ts/preferences.ts', import.meta.url), 'utf8'));
const flushAsync = () => new Promise(setImmediate);

test('time divider helper separates first turn, 5+ minute gaps, and day boundaries while ignoring legacy nulls', () => {
    const base = new Date(2026, 10, 26, 14, 20, 0).getTime();
    assert.equal(chatFormat.shouldShowTimeDivider(null, null), false);
    assert.equal(chatFormat.shouldShowTimeDivider(undefined, base), false);
    assert.equal(chatFormat.shouldShowTimeDivider(base, null), true);
    assert.equal(chatFormat.shouldShowTimeDivider(base + 4 * 60 * 1000, base), false);
    assert.equal(chatFormat.shouldShowTimeDivider(base + 5 * 60 * 1000, base), true);

    const beforeMidnight = new Date(2026, 10, 25, 23, 59, 0).getTime();
    const afterMidnight = new Date(2026, 10, 26, 0, 1, 0).getTime();
    assert.equal(chatFormat.shouldShowTimeDivider(afterMidnight, beforeMidnight), true);

    const now = new Date(2026, 10, 26, 18, 0, 0);
    assert.equal(chatFormat.formatChatTimestamp(base, now), '今天 14:20');
    assert.equal(chatFormat.formatChatTimestamp(beforeMidnight, now), '昨天 23:59');
    assert.equal(chatFormat.formatChatTimestamp(new Date(2026, 9, 5, 9, 5).getTime(), now), '10月5日 09:05');
    assert.equal(chatFormat.formatChatTimestamp(new Date(2025, 11, 31, 8, 30).getTime(), now), '2025年12月31日 08:30');
});

test('quote helpers compose, unwrap nested quotes, and parse cleanly', () => {
    const composed = chatFormat.composeQuotedMessage('明天要带伞吗？', {
        speaker: '小晚',
        text: '杭州今天有小雨\n记得穿暖和一点',
    });
    assert.equal(composed, '【引用 小晚：杭州今天有小雨 记得穿暖和一点】\n明天要带伞吗？');
    const parsed = chatFormat.parseQuotedMessage(composed);
    assert.deepEqual(parsed, {
        quote: {speaker: '小晚', text: '杭州今天有小雨 记得穿暖和一点'},
        body: '明天要带伞吗？',
    });
    const nested = chatFormat.composeQuotedMessage('我也觉得', {speaker: '你', text: composed});
    assert.equal(nested, '【引用 你：明天要带伞吗？】\n我也觉得');
});

test('useChat cancels in-flight sends back to draft/quote and records timestamps on saved turns', async () => {
    const canceledIds = [];
    let chatHandler;

    class AuthRequiredError extends Error {}
    class CancelledRequestError extends Error {}
    class UserCancelledChatError extends Error {}
    class ChatNotStartedError extends Error {
        constructor(message, retryable = true) { super(message); this.retryable = retryable; }
    }

    const useChatModule = compileModule(fs.readFileSync(new URL('../src/composables/useChat.ts', import.meta.url), 'utf8'), {
        '../ts/api.ts': {
            AuthRequiredError,
            CancelledRequestError,
            UserCancelledChatError,
            ChatNotStartedError,
            getRequestGeneration: () => 0,
            chatAPI: (text, options) => chatHandler(text, options),
            cancelChatAPI: async turnId => { canceledIds.push(turnId); return {ok: true, cancelled: true}; },
            historyAPI: async () => ({history_messages: []}),
        },
        '../ts/global_config.ts': {inputLimits: {max_message_chars: 8000}},
        '../ts/chat_format.ts': chatFormat,
    });

    const alerts = [];
    const state = useChatModule.useChat({
        canSend: () => true,
        showAlert: msg => alerts.push(msg),
        onBusyChange() {},
        scrollAfterRender() {},
    });

    // 1. Quote + cancel restores quote and draft without leaving a bubble or stage status
    let capturedOptions;
    chatHandler = (_text, options) => new Promise(() => { capturedOptions = options; });
    state.setQuote('小晚', '今天18°C');
    state.draft.value = '明天呢？';
    const pendingSend = state.sendMessage();
    await flushAsync();
    assert.equal(state.cancellable.value, true);
    assert.equal(state.entries.value.length, 1);
    assert.ok(capturedOptions.turnId);
    assert.equal('typingStatus' in state, false);

    state.cancelSend();
    await pendingSend;
    assert.equal(state.entries.value.length, 0);
    assert.equal(state.cancellable.value, false);
    assert.equal(state.busy.value, false);
    assert.equal(state.draft.value, '明天呢？');
    assert.deepEqual(state.quote.value, {speaker: '小晚', text: '今天18°C'});
    assert.deepEqual(canceledIds, [capturedOptions.turnId]);

    // 2. Normal send with quote succeeds and records round + createdAt
    let sentText = '';
    chatHandler = async text => {
        sentText = text;
        return {response: ['第一版回复'], conversation_round: 4, saved: true, created_at: 1764150000000};
    };
    await state.sendMessage();
    assert.equal(sentText, '【引用 小晚：今天18°C】\n明天呢？');
    assert.equal(state.entries.value.length, 1);
    assert.equal(state.entries.value[0].round, 4);
    assert.equal(state.entries.value[0].createdAt, 1764150000000);
    assert.deepEqual(state.entries.value[0].replies, ['第一版回复']);
    assert.deepEqual(alerts, []);

    // 3. Streamed bubbles pace naturally one by one instead of dumping simultaneously, then unlock cleanly
    let finishStream;
    let streamOnBubble;
    chatHandler = (_text, options) => {
        streamOnBubble = options.onBubble;
        return new Promise(resolve => { finishStream = resolve; });
    };
    state.draft.value = '流式测试';
    const streamingSend = state.sendMessage();
    await flushAsync();
    assert.equal(typeof streamOnBubble, 'function');
    streamOnBubble('第一条');
    streamOnBubble('第二条');
    // 两条流式气泡即使同一毫秒到达，也不会瞬间同时蹦出，而是先等首条节奏再间隔显示第二条
    assert.deepEqual(state.entries.value[1].replies, []);
    await new Promise(resolve => setTimeout(resolve, 760));
    assert.deepEqual(state.entries.value[1].replies, ['第一条']);
    assert.equal(state.isTyping.value, true);
    finishStream({
        response: ['第一条', '第二条'],
        conversation_round: 5,
        saved: true,
        created_at: 1764150005000,
    });
    await streamingSend;
    assert.equal(state.entries.value[1].round, 5);
    assert.deepEqual(state.entries.value[1].replies, ['第一条', '第二条']);
    assert.equal(state.isTyping.value, false);
    assert.equal(state.busy.value, false);

    // 4. Subsequent message after a multi-bubble streamed turn sends cleanly without getting stuck
    chatHandler = async () => ({response: ['下一轮回复'], conversation_round: 6, saved: true});
    state.draft.value = '继续发第二句';
    await state.sendMessage();
    assert.equal(state.entries.value.length, 3);
    assert.deepEqual(state.entries.value[2].replies, ['下一轮回复']);
    assert.equal(state.busy.value, false);
});

test('useChat supports image/document uploads, friendly non-vision model guard, cancel restore, and quote re-attach', async () => {
    let visionSupported = false;
    let chatCalls = [];
    let uploadedPayloads = [];
    let pendingChatResolve;
    let reconcileHistoryCalls = 0;

    class AuthRequiredError extends Error {}
    class CancelledRequestError extends Error {}
    class UserCancelledChatError extends Error {}
    class ChatNotStartedError extends Error {
        constructor(message, retryable = true) { super(message); this.retryable = retryable; }
    }

    let uploadSeq = 0;
    const useChatModule = compileModule(fs.readFileSync(new URL('../src/composables/useChat.ts', import.meta.url), 'utf8'), {
        '../ts/api.ts': {
            AuthRequiredError,
            CancelledRequestError,
            UserCancelledChatError,
            ChatNotStartedError,
            getRequestGeneration: () => 0,
            uploadAttachmentAPI: async payload => {
                uploadedPayloads.push(payload);
                const id = `att_${String(++uploadSeq).padStart(16, '0')}`;
                const kind = payload.mime_type.startsWith('image/') ? 'image' : 'document';
                return {id, kind, name: payload.filename, mime_type: payload.mime_type || 'text/plain', size: 128, url: `/attachments/${id}`};
            },
            chatAPI: (text, options) => {
                chatCalls.push({text, options});
                if (pendingChatResolve === 'hang') return new Promise(() => {});
                if (pendingChatResolve === 'stream_unsaved_error') {
                    options?.onBubble?.('等我查一下哈');
                    return Promise.reject(new ChatNotStartedError('AI 处理失败，请稍后重试', true));
                }
                return Promise.resolve({response: ['收到啦'], conversation_round: chatCalls.length, saved: true});
            },
            cancelChatAPI: async () => ({ok: true, cancelled: true}),
            historyAPI: async () => {
                reconcileHistoryCalls++;
                return {history_messages: []};
            },
        },
        '../ts/global_config.ts': {inputLimits: {max_message_chars: 8000}},
        '../ts/chat_format.ts': chatFormat,
    });

    const alerts = [];
    const state = useChatModule.useChat({
        canSend: () => true,
        visionReady: () => visionSupported,
        showAlert: msg => alerts.push(msg),
        onBusyChange() {},
        scrollAfterRender() {},
    });

    const fakeFile = (name, type, content = 'hello') => ({
        name,
        type,
        size: content.length,
        arrayBuffer: async () => new TextEncoder().encode(content).buffer,
    });

    // 1. 当前模型不支持视觉时：添加图片给出友好提示且不上传；添加文档正常上传并发送
    await state.addFiles([fakeFile('photo.png', 'image/png', 'pngbytes')]);
    assert.equal(uploadedPayloads.length, 0);
    assert.match(alerts.at(-1), /暂不支持图片识别/);

    await state.addFiles([fakeFile('notes.md', 'text/markdown', '# 笔记')]);
    assert.equal(state.attachments.value.length, 1);
    assert.equal(state.attachments.value[0].kind, 'document');
    await state.sendMessage();
    assert.equal(chatCalls.length, 1);
    assert.deepEqual(chatCalls[0].options.attachmentIds, ['att_0000000000000001']);
    assert.equal(state.attachments.value.length, 0);

    // 2. 切换为支持视觉的模型后：纯发图片（无文字）可发送，中途停止时附件回填到待发送栏
    visionSupported = true;
    await state.addFiles([fakeFile('cat.png', 'image/png', 'pngbytes')]);
    assert.equal(state.attachments.value.length, 1);
    pendingChatResolve = 'hang';
    const hanging = state.sendMessage();
    await flushAsync();
    assert.equal(state.attachments.value.length, 0);
    state.cancelSend();
    await hanging;
    assert.equal(state.attachments.value.length, 1);
    assert.equal(state.attachments.value[0].name, 'cat.png');

    // 重新正常发出图片，随后引用该图片气泡追问时携带 quotedAttachmentIds
    pendingChatResolve = null;
    await state.sendMessage();
    const imgTurn = state.entries.value.at(-1);
    state.setQuote('你', imgTurn.user, imgTurn.attachments);
    state.draft.value = '放大看看胡须';
    await state.sendMessage();
    assert.deepEqual(chatCalls.at(-1).options.quotedAttachmentIds, ['att_0000000000000002']);

    // 3. 流式未落库错误直接清理半截气泡并在气泡旁标红色感叹号可重发，不弹弹窗
    const alertsBeforeFail = alerts.length;
    pendingChatResolve = 'stream_unsaved_error';
    state.draft.value = '帮我查今天天气';
    await state.sendMessage();
    const failedTurn = state.entries.value.at(-1);
    assert.equal(reconcileHistoryCalls, 0);
    assert.deepEqual(failedTurn.replies, []);
    assert.equal(failedTurn.failure?.retryable, true);
    assert.equal(alerts.length, alertsBeforeFail);
});

test('ChatComposer switches between send and stop, and displays/clears quote bar', async () => {
    const originalResizeObserver = globalThis.ResizeObserver;
    globalThis.ResizeObserver = class { observe() {} disconnect() {} };
    let app;
    try {
        let cancelled = 0;
        let quoteCleared = 0;
        app = mountComponent(new URL('../src/components/ChatComposer.vue', import.meta.url), {
            imports: {'../ts/preferences.ts': prefs},
            props: {
                locked: true,
                cancellable: true,
                shortcut: 'enter',
                quote: {speaker: '小晚', text: '早饭吃咸口'},
                modelValue: '',
                'onUpdate:modelValue'() {},
                onCancel: () => { cancelled++; },
                onClearQuote: () => { quoteCleared++; },
            },
        });
        assert.ok(app.element('chat-quote-bar').text.includes('引用 小晚：'));
        const submit = app.element('chat-submit-btn');
        assert.equal(submit.text.trim(), '停止');
        assert.equal(submit.props.disabled, false);
        app.act('chat-submit-btn');
        assert.equal(cancelled, 1);
        app.act('chat-quote-clear');
        assert.equal(quoteCleared, 1);
    } finally {
        app?.unmount();
        if (originalResizeObserver === undefined) delete globalThis.ResizeObserver;
        else globalThis.ResizeObserver = originalResizeObserver;
    }
});
