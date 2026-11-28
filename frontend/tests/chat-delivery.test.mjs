import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
import {runInNewContext} from 'node:vm';
import {compileModule, mountComponent, nextTick} from './vue-harness.mjs';

const chatFormat = compileModule(fs.readFileSync(new URL('../src/ts/chat_format.ts', import.meta.url), 'utf8'));
const sourcePath = new URL('../src/composables/useChat.ts', import.meta.url);
const compiled = fs.existsSync(sourcePath) && ts.transpileModule(fs.readFileSync(sourcePath, 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}
}).outputText;

class ChatNotStartedError extends Error {
    constructor(message, retryable = true) {
        super(message);
        this.retryable = retryable;
    }
}

const saved = (round, user = '原文', assistant = ['回复']) => ({conversation_round: round, user, assistant});
const flushAsync = () => new Promise(setImmediate);

async function harness({chatAPI, historyAPI, maxMessageChars = 8000} = {}) {
    assert.ok(compiled, 'the Vue chat state module is missing');
    const vue = await import('vue');
    const alerts = [];
    const timings = [];
    const locks = [];
    const delays = [];
    let resolveDelay;
    let generation = 0;
    const exports = {};
    runInNewContext(compiled, {
        exports,
        require(name) {
            if (name === 'vue') return vue;
            if (name === '../ts/api.ts') return {
                chatAPI, historyAPI, ChatNotStartedError,
                AuthRequiredError: class extends Error {}, CancelledRequestError: class extends Error {},
                getRequestGeneration: () => generation
            };
            if (name === '../ts/global_config.ts') return {inputLimits: {max_message_chars: maxMessageChars}};
            if (name === '../ts/chat_format.ts') return chatFormat;
            throw new Error('Unexpected dependency: ' + name);
        },
        performance: {now: () => 1, clearMeasures() {}, measure: name => timings.push(name)},
        setTimeout(callback, delay) { resolveDelay = callback; delays.push(delay); return 1; },
        clearTimeout() { resolveDelay = undefined; }
    });
    const state = exports.useChat({
        canSend: () => true,
        showAlert: message => alerts.push(message),
        onBusyChange: busy => locks.push(busy),
        scrollAfterRender() {}
    });
    state.draft.value = '原文';
    return {...state, alerts, timings, locks, delays,
        nextReply: async () => { resolveDelay?.(); await flushAsync(); },
        nextAccount: () => { generation++; state.reset(); }
    };
}

// Copy data out of the VM so strict assertions compare objects from the same realm.
const snapshot = value => JSON.parse(JSON.stringify(value));

test('a fast first reply waits 700 ms after sending without showing a typing bubble', async () => {
    const app = await harness({async chatAPI() {
        return {response: ['回复'], conversation_round: 1, saved: true};
    }});
    const sending = app.sendMessage();
    await flushAsync();
    const [turn] = app.entries.value;
    assert.equal(turn.user, '原文');
    assert.deepEqual(snapshot(turn.replies), []);
    assert.equal(app.isTyping.value, false);
    assert.equal(app.busy.value, true);
    assert.deepEqual(app.delays, [700]);
    await app.nextReply();
    await sending;
    assert.deepEqual(snapshot(turn.replies), ['回复']);
    assert.equal(app.isTyping.value, false);
});

test('a slow first reply shows typing after 700 ms and needs no additional delay', async () => {
    let resolveSend;
    const app = await harness({chatAPI: () => new Promise(resolve => { resolveSend = resolve; })});
    const sending = app.sendMessage();
    assert.equal(app.isTyping.value, false);
    await app.nextReply();
    assert.equal(app.isTyping.value, true);
    resolveSend({response: ['回复'], conversation_round: 1, saved: true});
    await sending;
    assert.deepEqual(snapshot(app.entries.value[0].replies), ['回复']);
    assert.deepEqual(app.delays, [700]);
    assert.equal(app.isTyping.value, false);
});

test('changing account cancels the first reply delay before a bubble appears', async () => {
    const app = await harness({async chatAPI() {
        return {response: ['旧账号回复'], conversation_round: 1, saved: true};
    }});
    const sending = app.sendMessage();
    await flushAsync();
    const [turn] = app.entries.value;
    app.nextAccount();
    await sending;
    assert.deepEqual(snapshot(turn.replies), []);
    assert.equal(app.entries.value.length, 0);
    assert.equal(app.isTyping.value, false);
    assert.equal(app.busy.value, false);
});

test('the chat limit counts Unicode characters and preserves an oversized draft', async () => {
    const sent = [];
    const app = await harness({maxMessageChars: 3, async chatAPI(text) {
        sent.push(text);
        return {response: ['回复'], conversation_round: 1, saved: true};
    }});
    const valid = '🙂🙂🙂';
    app.draft.value = valid + '好';
    await app.sendMessage();
    assert.deepEqual(sent, []);
    assert.equal(app.draft.value, valid + '好');
    assert.match(app.alerts[0], /3/);
    app.draft.value = valid;
    const sending = app.sendMessage();
    await app.nextReply();
    await sending;
    assert.deepEqual(sent, [valid]);
});

test('a retryable rejection resends the original, retains its key, and keeps the new draft', async () => {
    const message = '请先在设置中配置个人模型和 API Key';
    const sent = [];
    const app = await harness({
        async chatAPI(text) {
            sent.push(text);
            if (sent.length === 1) throw new ChatNotStartedError(message);
            return {response: ['个人模型回复'], conversation_round: 1, saved: true};
        },
        async historyAPI() { assert.fail('definite rejection does not need history'); }
    });
    const sending = app.sendMessage();
    assert.equal(app.isTyping.value, false);
    app.draft.value = '新草稿';
    await sending;
    const [turn] = app.entries.value;
    assert.equal(turn.failure.message, message);
    assert.equal(turn.failure.retryable, true);
    assert.deepEqual(app.alerts, []);
    assert.equal(app.isTyping.value, false);
    const retrying = app.activateFailure(turn);
    await app.nextReply();
    await retrying;
    assert.deepEqual(sent, ['原文', '原文']);
    assert.equal(app.entries.value.length, 1);
    assert.equal(app.entries.value[0].id, turn.id);
    assert.equal(turn.round, 1);
    assert.equal(turn.failure, undefined);
    assert.equal(app.draft.value, '新草稿');
    assert.deepEqual(snapshot(turn.replies), ['个人模型回复']);
    assert.deepEqual(app.timings, ['xiaowan.chat.first-reply']);
    assert.deepEqual(app.locks, [true, false, true, false]);
});

test('a permanent rejection marks the bubble without an immediate popup and shows its reason when activated', async () => {
    let calls = 0;
    const app = await harness({async chatAPI() {
        calls++;
        throw new ChatNotStartedError('消息过长', false);
    }});
    await app.sendMessage();
    assert.deepEqual(app.alerts, []);
    await app.activateFailure(app.entries.value[0]);
    assert.equal(calls, 1);
    assert.deepEqual(app.alerts, ['消息过长']);
});

test('a transport error silently adopts a newly saved round or marks the bubble with a retryable exclamation', async () => {
    let calls = 0;
    const item = saved(1, '原文', ['已保存的回复']);
    const savedApp = await harness({
        async chatAPI() { calls++; throw new Error('transport closed'); },
        async historyAPI() { return {history_messages: [item]}; }
    });
    await savedApp.sendMessage();
    assert.equal(calls, 1);
    assert.equal(savedApp.entries.value.length, 1);
    assert.equal(savedApp.entries.value[0].round, 1);
    assert.deepEqual(snapshot(savedApp.entries.value[0].replies), ['已保存的回复']);
    assert.equal(savedApp.entries.value[0].failure, undefined);
    assert.deepEqual(savedApp.alerts, []);

    let retryCalls = 0;
    const failedApp = await harness({
        async chatAPI() {
            retryCalls++;
            if (retryCalls === 1) throw new Error('network down');
            return {response: ['重发成功'], conversation_round: 0, saved: true};
        },
        async historyAPI() { throw new Error('offline'); }
    });
    await failedApp.sendMessage();
    const [failedTurn] = failedApp.entries.value;
    assert.equal(failedTurn.user, '原文');
    assert.equal(failedTurn.failure.retryable, true);
    assert.deepEqual(failedApp.alerts, []);

    const retrying = failedApp.activateFailure(failedTurn);
    await failedApp.nextReply();
    await retrying;
    assert.equal(failedTurn.failure, undefined);
    assert.equal(failedTurn.round, 0);
    assert.deepEqual(snapshot(failedTurn.replies), ['重发成功']);
});

test('reply delay is recalculated for each bubble and never waits after the last', async () => {
    const replies = ['hello', 'a'.repeat(20), 'b'.repeat(100), 'last'];
    const app = await harness({
        async chatAPI() { return {response: replies, conversation_round: null, saved: false}; }
    });
    const sending = app.sendMessage();
    await app.nextReply();
    const [turn] = app.entries.value;
    assert.deepEqual(snapshot(turn.replies), ['hello']);
    assert.deepEqual(app.timings, ['xiaowan.chat.first-reply']);
    assert.equal(app.isTyping.value, false);
    await app.nextReply();
    await app.nextReply();
    await app.nextReply();
    await sending;
    assert.deepEqual(app.delays, [700, 1500, 3000, 5000]);
    assert.deepEqual(snapshot(turn.replies), replies);
    assert.equal(turn.round, null);
    assert.match(app.alerts[0], /未保存/);
});

test('changing account cancels delayed replies, clears the draft, and unlocks the chat', async () => {
    const app = await harness({
        async chatAPI() { return {response: ['first', 'second', 'third'], conversation_round: 0, saved: true}; }
    });
    const sending = app.sendMessage();
    await app.nextReply();
    const [turn] = app.entries.value;
    assert.deepEqual(snapshot(turn.replies), ['first']);
    app.draft.value = '新草稿';
    app.nextAccount();
    await sending;
    assert.deepEqual(snapshot(turn.replies), ['first']);
    assert.equal(app.entries.value.length, 0);
    assert.equal(app.draft.value, '');
    assert.equal(app.busy.value, false);
});

test('history merges missing rounds in order and keeps unsent bubbles and stable keys', async () => {
    let history = [saved(0), saved(2)];
    const app = await harness({
        async chatAPI() { throw new ChatNotStartedError('发送失败'); },
        async historyAPI() { return {history_messages: history}; }
    });
    await app.loadHistory();
    await app.sendMessage();
    const keys = [...app.entries.value.map(turn => turn.id)];
    history = [saved(0), saved(1), saved(2)];
    await app.loadHistory();
    assert.deepEqual([...app.entries.value.map(turn => turn.round)], [0, 1, 2, null]);
    assert.deepEqual([...app.entries.value.filter(turn => turn.round !== 1).map(turn => turn.id)], keys);
});

test('a history refresh and a successful send with the same round converge on the optimistic key', async () => {
    let resolveSend;
    let history = [saved(0), saved(1)];
    const app = await harness({
        chatAPI: () => new Promise(resolve => { resolveSend = resolve; }),
        async historyAPI() { return {history_messages: history}; }
    });
    await app.loadHistory();
    app.draft.value = '另一个本地消息';
    const sending = app.sendMessage();
    const optimisticKey = app.entries.value.at(-1).id;
    // Simulate a second history request returning the just-saved round before the send response.
    history = [...history, saved(2, '另一个本地消息', ['另外的回复']), saved(3)];
    await app.loadHistory();
    resolveSend({response: ['另外的回复'], conversation_round: 2, saved: true});
    await app.nextReply();
    await sending;
    const turns = app.entries.value;
    assert.deepEqual([...turns.map(turn => turn.round)], [0, 1, 2, 3]);
    assert.equal(turns[2].id, optimisticKey);
});

test('a send confirmation stays in round order when older history arrives during the send', async () => {
    let resolveSend;
    let history = [saved(0)];
    const app = await harness({
        chatAPI: () => new Promise(resolve => { resolveSend = resolve; }),
        async historyAPI() { return {history_messages: history}; }
    });
    await app.loadHistory();
    const sending = app.sendMessage();
    const optimisticKey = app.entries.value.at(-1).id;
    history = [...history, saved(1), saved(2)];
    await app.loadHistory();
    resolveSend({response: ['最新的回复'], conversation_round: 3, saved: true});
    await app.nextReply();
    await sending;
    assert.deepEqual(Array.from(app.entries.value, turn => turn.round), [0, 1, 2, 3]);
    assert.equal(app.entries.value.at(-1).id, optimisticKey);
});

test('clearing history keeps the draft and ignores in-flight history from before clearing', async () => {
    let resolveHistory;
    const app = await harness({historyAPI: () => new Promise(resolve => { resolveHistory = resolve; })});
    const loading = app.loadHistory();
    app.clearHistory();
    resolveHistory({history_messages: [saved(0)]});
    await loading;
    assert.equal(app.entries.value.length, 0);
    assert.equal(app.draft.value, '原文');
});

async function workspaceHarness(t, initialEntries = []) {
    const {ref} = await import('vue');
    const entries = ref(initialEntries);
    const body = {};
    let activeElement;
    const globals = {
        window: new EventTarget(),
        document: {body, get activeElement() { return activeElement?.parent ? activeElement : body; }},
        ResizeObserver: class { observe() {} disconnect() {} },
        Element: class { closest(selector) { return selector === 'dialog[open]' ? this : null; } }
    };
    const originals = new Map();
    for (const [name, value] of Object.entries(globals)) {
        originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name));
        Object.defineProperty(globalThis, name, {configurable: true, value});
    }
    let app;
    t.after(() => {
        app?.unmount();
        for (const [name, original] of originals) {
            if (original) Object.defineProperty(globalThis, name, original);
            else delete globalThis[name];
        }
    });
    app = mountComponent(new URL('../src/components/ChatWorkspace.vue', import.meta.url), {
        imports: {
            './ChatComposer.vue': {__esModule: true, default: {render: () => null}},
            '../composables/useChat.ts': {useChat: () => ({
                entries, draft: ref(''), busy: ref(false), isTyping: ref(false), reset() {}
            })},
            '../ts/chat_format.ts': chatFormat,
        },
        props: {ready: true, shortcut: 'enter', profile: null, persona: null, clearingHistory: false, showAlert() {}}
    });
    const toggle = app.element('sidebar-zone').children[0];
    toggle.props.onClick();
    await nextTick();
    const list = app.element('review-list');
    list.querySelectorAll = () => list.children.filter(child => child.tag === 'button');
    list.contains = node => node?.parent === list;
    for (const result of list.querySelectorAll()) {
        result.focus = () => { activeElement = result; };
    }
    return {entries, toggle, list, ...globals};
}

test('history search keeps keyboard focus when an unconfirmed turn is removed', async t => {
    const app = await workspaceHarness(t, [
        {kind: 'turn', id: 'round-0', round: 0, user: '旧消息', replies: ['旧回复']},
        {kind: 'turn', id: 'local-1', round: null, user: '新消息', replies: []}
    ]);
    const [optimistic, older] = app.list.querySelectorAll();
    optimistic.focus();
    app.entries.value.splice(1, 1);
    await nextTick();
    await nextTick();
    assert.ok(app.document.activeElement === older, 'focus should move to the remaining result');
});

test('Escape in a modal leaves the history sidebar open', async t => {
    const app = await workspaceHarness(t);
    const escape = Object.assign(new Event('keydown', {cancelable: true, bubbles: true}), {key: 'Escape'});
    Object.defineProperty(escape, 'target', {value: new app.Element()});
    assert.equal(app.toggle.props['aria-expanded'], true);
    app.window.dispatchEvent(escape);
    await nextTick();
    assert.equal(app.toggle.props['aria-expanded'], true);
});
