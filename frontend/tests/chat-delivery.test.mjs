import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
import {runInNewContext} from 'node:vm';

const source = fs.readFileSync(new URL('../src/ts/chat.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;

function harness(chatAPI, historyAPI) {
    const rows = [];
    const confirmed = [];
    const sendBtn = {disabled: false};
    const inputBox = {value: '原文'};
    const history = [];
    const timings = [];
    const alerts = [];
    class ChatNotStartedError extends Error {
        constructor(message, retryable = true) {
            super(message);
            this.retryable = retryable;
        }
    }
    function element(tag) {
        return {
            tag, children: [], isConnected: true, events: {}, dataset: {},
            append(...children) { this.children.push(...children); },
            prepend(child) { this.children.unshift(child); },
            addEventListener(name, handler) { this.events[name] = handler; },
            setAttribute() {},
            remove() {
                this.isConnected = false;
                const index = rows.indexOf(this);
                if (index >= 0) rows.splice(index, 1);
            }
        };
    }
    const exports = {};
    runInNewContext(compiled, {
        exports,
        require(name) {
            if (name === './api.ts') return {
                chatAPI: text => chatAPI(text, ChatNotStartedError, inputBox), historyAPI,
                AuthRequiredError: class extends Error {}, CancelledRequestError: class extends Error {},
                ChatNotStartedError, getRequestGeneration: () => 0
            };
            if (name === './ui.ts') return {
                inputBox, sendBtn, chatBox: {appendChild: row => { row.remove(); row.isConnected = true; rows.push(row); }},
                lockSendBtn() { sendBtn.disabled = true; }, unlockSendBtn() { sendBtn.disabled = false; },
                showTypingIndicator() {}, hideTypingIndicator() {}, scrollToBottom() {}, showAlert(message) { alerts.push(message); }
            };
            if (name === './global_config.ts') return {modelConfig: {model_valid: true}};
            return {
                addUserMessage(text) { const row = element('user'); row.textContent = text; rows.push(row); return row; },
                async addAssistantMessage(messages, _isCurrent, firstReply) { confirmed.push(...messages); firstReply?.(); },
                showChatHistory(items) { history.push(...items); }
            };
        },
        document: {createElement: element, getElementById: () => inputBox},
        window: {alert() {}},
        performance: {now: () => 1, clearMeasures() {}, measure: name => timings.push(name)},
        requestAnimationFrame: callback => callback()
    });
    return {...exports, rows, confirmed, inputBox, history, timings, alerts};
}

test('the chat limit counts Unicode characters and preserves an oversized draft', async () => {
    const sent = [];
    const app = harness(async text => {
        sent.push(text);
        return {response: ['回复'], conversation_round: 1, saved: true};
    });
    const valid = '🙂'.repeat(8000);
    app.inputBox.value = valid + '好';
    await app.sendMessage();
    assert.equal(sent.length, 0);
    assert.equal(app.inputBox.value, valid + '好');
    assert.match(app.alerts[0], /8000/);
    app.inputBox.value = valid;
    await app.sendMessage();
    assert.deepEqual(sent, [valid]);
});

test('a definite rejection offers retry for the original message without replacing the current draft', async () => {
    const sent = [];
    const app = harness(async (text, Rejected, input) => {
        sent.push(text);
        if (sent.length === 1) { input.value = '新草稿'; throw new Rejected('当前聊天较忙'); }
        return {response: ['回复'], conversation_round: 1, saved: true};
    }, async () => assert.fail('rejection must not need history'));
    await app.sendMessage();
    assert.equal(app.inputBox.value, '新草稿');
    assert.equal(app.rows.length, 1);
    await app.rows[0].children[0].children.find(child => child.tag === 'button').events.click();
    assert.deepEqual(sent, ['原文', '原文']);
    assert.equal(app.rows.length, 1);
    assert.equal(app.inputBox.value, '新草稿');
    assert.deepEqual(app.confirmed, ['回复']);
    assert.deepEqual(app.timings, ['xiaowan.chat.first-reply']);
});

test('server validation leaves the original message visible without offering an identical retry', async () => {
    const app = harness(async (_text, Rejected) => { throw new Rejected('消息不符合要求', false); },
        async () => assert.fail('validation must not need history'));
    await app.sendMessage();
    assert.equal(app.rows[0].textContent, '原文');
    assert.equal(app.rows[0].children[0].children.some(child => child.tag === 'button'), false);
});

test('an uncertain send refreshes history without offering a duplicate send', async () => {
    let calls = 0;
    const saved = {conversation_round: 1, user: '原文', assistant: ['已保存的回复']};
    const app = harness(async () => { calls++; throw new Error('transport closed'); }, async () => ({history_messages: [saved]}));
    await app.sendMessage();
    assert.deepEqual(app.history, [saved]);
    assert.equal(calls, 1);
    assert.equal(app.rows.some(row => row.tag === 'user'), false);
    assert.equal(app.rows.length, 1);
    assert.equal(app.rows[0].children.some(child => child.tag === 'button'), false);
});
