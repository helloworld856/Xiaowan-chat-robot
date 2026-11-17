import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
import {runInNewContext} from 'node:vm';

const sourcePath = new URL('../src/ts/preferences.ts', import.meta.url);
let prefs;
try {
    const source = fs.readFileSync(sourcePath, 'utf8');
    const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ESNext}}).outputText;
    prefs = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'));
} catch (error) {
    prefs = {loadError: error};
}

test('Enter sends, Shift+Enter and IME confirmation preserve input', () => {
    assert.equal(typeof prefs.shouldSendMessage, 'function', 'keyboard preferences not implemented');
    const enter = {key: 'Enter', shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, isComposing: false, keyCode: 13};
    assert.equal(prefs.shouldSendMessage(enter, 'enter'), true);
    assert.equal(prefs.shouldSendMessage({...enter, shiftKey: true}, 'enter'), false);
    assert.equal(prefs.shouldSendMessage({...enter, isComposing: true}, 'enter'), false);
    assert.equal(prefs.shouldSendMessage({...enter, keyCode: 229}, 'enter'), false);
    assert.equal(prefs.shouldSendMessage({...enter, key: 'a'}, 'enter'), false);
});

test('Ctrl+Enter mode leaves ordinary Enter for newlines', () => {
    assert.equal(typeof prefs.shouldSendMessage, 'function');
    const enter = {key: 'Enter', shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, isComposing: false, keyCode: 13};
    assert.equal(prefs.shouldSendMessage(enter, 'ctrl_enter'), false);
    assert.equal(prefs.shouldSendMessage({...enter, ctrlKey: true}, 'ctrl_enter'), true);
    assert.equal(prefs.shouldSendMessage({...enter, metaKey: true}, 'ctrl_enter'), true);
    assert.equal(prefs.shouldSendMessage({...enter, ctrlKey: true, shiftKey: true}, 'ctrl_enter'), false);
});

test('message rendering recalculates delay for each reply and never waits after the last', async () => {
    const source = fs.readFileSync(new URL('../src/ts/utils.ts', import.meta.url), 'utf8');
    const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
    const exports = {};
    const delays = [];
    const rendered = [];
    const diagnostics = [];
    const element = () => ({append() {}, appendChild() {}});
    runInNewContext(compiled, {
        exports,
        require: name => name === './ui.ts'
            ? {chatBox: {scrollTop: 0, clientHeight: 0, scrollHeight: 0, appendChild: row => rendered.push(row)}, scrollToBottom() {}}
            : (() => { throw new Error('Unexpected dependency: ' + name); })(),
        window: {USER_AVATAR: '', BOT_AVATAR: ''}, document: {createElement: element},
        console: {log: message => diagnostics.push(message)},
        setTimeout: (resolve, milliseconds) => { delays.push(milliseconds); resolve(); }
    });
    const firstReply = [];
    await exports.addAssistantMessage(['hello', 'a'.repeat(20), 'b'.repeat(100), 'last'], undefined,
        () => firstReply.push({rows: rendered.length, delays: delays.length}));
    assert.deepEqual(firstReply, [{rows: 1, delays: 0}]);
    assert.deepEqual(delays, [1500, 3000, 5000]);
    assert.deepEqual(diagnostics, [
        '收到 4 条助手回复',
        '下一条助手消息将在 1500 ms 后显示',
        '下一条助手消息将在 3000 ms 后显示',
        '下一条助手消息将在 5000 ms 后显示'
    ]);
    assert.equal(rendered.length, 4);
    delays.length = 0;
    await exports.addAssistantMessage(['only one reply']);
    assert.deepEqual(delays, []);
    assert.equal(rendered.length, 5);
});

// 在两条气泡的显示间隔内退出，验证剩余旧回复被丢弃。
test('logout stops remaining delayed reply bubbles from the previous page', async () => {
    const source = fs.readFileSync(new URL('../src/ts/utils.ts', import.meta.url), 'utf8');
    const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
    const exports = {}, rendered = [];
    let active = true;
    runInNewContext(compiled, {
        exports, require: () => ({chatBox: {scrollTop: 0, clientHeight: 0, scrollHeight: 0, appendChild: row => rendered.push(row)}, scrollToBottom() {}}),
        window: {USER_AVATAR: '', BOT_AVATAR: ''}, document: {createElement: () => ({append() {}, appendChild() {}})},
        console: {log() {}}, setTimeout: resolve => { active = false; resolve(); }
    });
    await exports.addAssistantMessage(['first', 'second', 'third'], () => active);
    assert.equal(rendered.length, 1);
});

test('history refresh inserts missing saved rounds in order and preserves unsent bubbles', () => {
    const source = fs.readFileSync(new URL('../src/ts/utils.ts', import.meta.url), 'utf8');
    const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
    const exports = {};
    const rows = [];
    const element = () => ({dataset: {}, children: [], append(...children) { this.children.push(...children); },
        appendChild(child) { this.children.push(child); }});
    const chatBox = {
        querySelectorAll: () => rows.filter(row => row.dataset.conversationRound !== undefined),
        appendChild: row => rows.push(row),
        insertBefore: (fragment, next) => rows.splice(next ? rows.indexOf(next) : rows.length, 0, ...fragment.children)
    };
    runInNewContext(compiled, {
        exports, require: () => ({chatBox, scrollToBottom() {}}),
        window: {USER_AVATAR: '', BOT_AVATAR: ''}, document: {createElement: element, createDocumentFragment: element}
    });
    const saved = round => ({conversation_round: round, user: `user-${round}`, assistant: [`bot-${round}`]});
    exports.showChatHistory([saved(0), saved(2)]);
    const unsent = exports.addUserMessage('unsent');
    exports.showChatHistory([saved(0), saved(1), saved(2)]);
    assert.deepEqual(rows.map(row => row.dataset.conversationRound), ['0', undefined, '1', undefined, '2', undefined, undefined]);
    assert.equal(rows.at(-1), unsent);
});
