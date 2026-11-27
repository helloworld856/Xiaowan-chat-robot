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

    // 3. Streamed bubbles appear immediately via onBubble before the turn resolves
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
    streamOnBubble('第一条流式气泡');
    assert.deepEqual(state.entries.value[1].replies, ['第一条流式气泡']);
    assert.equal(state.isTyping.value, false);
    streamOnBubble('第二条流式气泡');
    assert.deepEqual(state.entries.value[1].replies, ['第一条流式气泡', '第二条流式气泡']);
    finishStream({
        response: ['第一条流式气泡', '第二条流式气泡'],
        conversation_round: 5,
        saved: true,
        created_at: 1764150005000,
    });
    await streamingSend;
    assert.equal(state.entries.value[1].round, 5);
    assert.deepEqual(state.entries.value[1].replies, ['第一条流式气泡', '第二条流式气泡']);
    assert.equal(state.busy.value, false);
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
