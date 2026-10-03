import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {runInNewContext} from 'node:vm';
import ts from 'typescript';

// 运行真实 TS 模块，仅替换网络、DOM 和其他模块的边界。
function runModule(file, modules, globals = {}) {
    const source = readFileSync(new URL(`../src/ts/${file}`, import.meta.url), 'utf8');
    const {outputText} = ts.transpileModule(source, {
        compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}
    });
    const exports = {};
    const done = runInNewContext(`(async () => { ${outputText}\n })()`, {
        exports, require: name => {
            assert.ok(name in modules, `缺少测试依赖 ${name}`);
            return modules[name];
        },
        console: {log() {}, warn() {}, error() {}},
        ...globals
    });
    return {exports, done};
}

function startApp(api = {}) {
    const loadingClasses = new Set();
    const warnings = [];
    const modelConfig = {model_valid: false, model: {model_merchant: '', model_name: ''}};
    let locked = false;
    let validated = false;
    const modules = {
        './storage.ts': {loadModel: () => ({model: {model_merchant: 'deepseek', model_name: 'example'}})},
        './ui.ts': {
            lockSendBtn() { locked = true; }, unlockSendBtn() { locked = false; },
            updateUi() {}, themeInput: [], chatBox: {}, showAlert: text => warnings.push(text)
        },
        './api.ts': {
            personaAPI: async () => ({BOT_NAME: '小晚', BOT_AVATAR: '', USER_AVATAR: '', BOT_BIRTHDAY: '', BOT_BIRTHPLACE: ''}),
            modelAPI: async () => { validated = true; return {status: true, info: ''}; },
            historyAPI: async () => ({num: 0, history_messages: []}), ...api
        },
        './utils.ts': {showChatHistory() {}, switchTheme() {}},
        './addevent.ts': {addEventToUi() {}},
        './global_config.ts': {modelConfig, personaConfig: {persona_valid: false, persona: {}}}
    };
    const {done} = runModule('main.ts', modules, {
        document: {getElementById: () => ({classList: {
            add: name => loadingClasses.add(name), remove: name => loadingClasses.delete(name)
        }})}, localStorage: {getItem: () => null}
    });
    return {done, loadingClasses, warnings, modelConfig, locked: () => locked, validated: () => validated};
}

test('历史接口失败后关闭加载画面，并向用户提示', async () => {
    const app = startApp({historyAPI: async () => { throw new Error('history unavailable'); }});
    await assert.doesNotReject(app.done);
    assert.equal(app.loadingClasses.has('close'), true);
    assert.equal(app.locked(), false);
    assert.equal(app.warnings.length, 1);
});

test('人格接口失败后仍尝试恢复模型配置', async () => {
    const app = startApp({personaAPI: async () => { throw new Error('persona unavailable'); }});
    await app.done;
    assert.equal(app.validated(), true);
    assert.equal(app.modelConfig.model_valid, true);
    assert.equal(app.warnings.length, 1);
});

test('模型验证失败时结束加载，并保留无效模型状态', async () => {
    const app = startApp({modelAPI: async () => ({status: false, info: 'invalid key'})});
    await app.done;
    assert.equal(app.modelConfig.model_valid, false);
    assert.equal(app.loadingClasses.has('close'), true);
    assert.equal(app.warnings.length, 1);
});

for (const [name, args] of [['personaAPI', []], ['historyAPI', [0]], ['modelAPI', [{model_merchant: 'deepseek', model_name: 'example'}]]]) {
    test(`${name} 在服务端无响应时自动超时`, async () => {
        const {exports: api, done} = runModule('api.ts', {}, {
            window: {location: {origin: 'http://localhost'}},
            AbortSignal: {timeout: () => AbortSignal.timeout(10)},
            fetch: async (_url, options) => {
                if (!options?.signal) return {ok: true, json: async () => ({})};
                return new Promise((_resolve, reject) => {
                    options.signal.addEventListener('abort', () => reject(options.signal.reason), {once: true});
                });
            }
        });
        await done;
        await Promise.all([
            assert.rejects(api[name](...args), error => error.name === 'TimeoutError'),
            new Promise(resolve => setTimeout(resolve, 30))
        ]);
    });
}

test('保存失败时仍显示回复，并给出未保存提示', async () => {
    const replies = [], warnings = [];
    const input = {value: '你好'};
    const {exports: chat, done} = runModule('chat.ts', {
        './api.ts': {chatAPI: async () => ({response: ['你好呀'], conversation_round: 1, saved: false})},
        './global_config.ts': {modelConfig: {model_valid: true}},
        './ui.ts': {
            sendBtn: {disabled: false}, lockSendBtn() {}, unlockSendBtn() {}, scrollToBottom() {},
            showTypingIndicator() {}, hideTypingIndicator() {}, showAlert: text => warnings.push(text)
        },
        './utils.ts': {addUserMessage: async () => {}, addAssistantMessage: async texts => replies.push(...texts)}
    }, {document: {getElementById: () => input}, requestAnimationFrame: callback => callback(), window: {alert: text => warnings.push(text)}});
    await done;
    await chat.sendMessage();
    assert.deepEqual(replies, ['你好呀']);
    assert.equal(warnings.length, 1);
});
