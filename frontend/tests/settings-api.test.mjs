import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';

globalThis.window = {location: {origin: 'http://local-test'}, dispatchEvent: () => true};
const source = fs.readFileSync(new URL('../src/ts/api.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ESNext}}).outputText;
const api = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'));

test('custom model address and case survive both connection-test and save requests', async t => {
    const calls = [];
    t.mock.method(globalThis, 'fetch', async (url, options) => {
        calls.push({url, payload: JSON.parse(options.body)});
        return new Response(JSON.stringify({status: true, info: 'ok'}));
    });
    const model = {model_merchant: 'deepseek', model_name: 'Custom-V2', base_url: 'https://example.com/v1'};
    await api.modelAPI(model, true);
    await api.modelAPI(model);
    assert.deepEqual(calls, [
        {url: 'http://local-test/model/test', payload: model},
        {url: 'http://local-test/model', payload: model}
    ]);
});

test('a personal API key is sent only to the personal model endpoints', async t => {
    const calls = [];
    t.mock.method(globalThis, 'fetch', async (url, options) => {
        calls.push({url, payload: JSON.parse(options.body)});
        return new Response(JSON.stringify({status: true}));
    });
    const model = {model_merchant: 'deepseek', model_name: 'User-Model', base_url: 'https://example.com/v1'};
    const payload = {model_merchant: 'deepseek', model_name: 'User-Model', api_key: 'test-personal-placeholder'};
    await api.modelAPI(model, true, payload.api_key);
    await api.modelAPI(model, false, payload.api_key);
    assert.deepEqual(calls, [
        {url: 'http://local-test/model/personal/test', payload},
        {url: 'http://local-test/model/personal', payload}
    ]);
});

test('private 401 requests return to login; wrong login passwords do not fire a second expiry event', async t => {
    const events = [];
    t.mock.method(window, 'dispatchEvent', event => { events.push(event.type); return true; });
    t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({detail: '请先登录'}), {status: 401}));
    await assert.rejects(api.historyAPI(0), error => error instanceof api.AuthRequiredError);
    assert.deepEqual(events, ['auth-required']);
    events.length = 0;
    assert.equal(typeof api.loginAPI, 'function', 'login API is missing');
    await assert.rejects(api.loginAPI('admin', 'wrong'), error => error instanceof api.AuthRequiredError);
    assert.deepEqual(events, []);
});

// 模拟 fetch 在退出后才完成，验证会话代数校验仍会丢弃响应。
test('responses arriving after logout cannot be used by the previous authenticated page', async t => {
    assert.equal(typeof api.invalidateRequests, 'function', 'request invalidation is missing');
    let resolve;
    t.mock.method(globalThis, 'fetch', () => new Promise(done => { resolve = done; }));
    const pending = api.historyAPI(0);
    api.invalidateRequests();
    resolve(new Response(JSON.stringify({history_messages: ['old private data']})));
    await assert.rejects(pending, error => error instanceof api.CancelledRequestError);
});

test('login/session/logout use same-origin cookies and send credentials only to login', async t => {
    assert.equal(typeof api.loginAPI, 'function', 'login API is missing');
    const calls = [];
    t.mock.method(globalThis, 'fetch', async (url, options) => {
        calls.push({url, method: options.method, credentials: options.credentials, body: options.body});
        return new Response(JSON.stringify({user: {username: 'admin'}}));
    });
    await api.loginAPI('admin', '123456');
    await api.sessionAPI();
    await api.logoutAPI();
    assert.deepEqual(calls.map(call => call.url), ['http://local-test/auth/login', 'http://local-test/auth/session', 'http://local-test/auth/logout']);
    assert.ok(calls.every(call => call.credentials === 'same-origin'));
    assert.deepEqual(JSON.parse(calls[0].body), {username: 'admin', password: '123456'});
    assert.equal(calls[1].body, undefined);
    assert.equal(calls[2].body, undefined);
});

test('a normal signed-out session response is returned without an auth failure event', async t => {
    const events = [];
    t.mock.method(window, 'dispatchEvent', event => { events.push(event.type); return true; });
    t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({user: null}), {status: 200}));
    assert.deepEqual(await api.sessionAPI(), {user: null});
    assert.deepEqual(events, []);
});

test('registration uses its own endpoint without the current account header', async t => {
    const calls = [];
    const user = {id: 7, username: 'new_user', role: 'user'};
    t.mock.method(globalThis, 'fetch', async (url, options) => {
        calls.push({url, ...options});
        return new Response(JSON.stringify({user}), {status: 201});
    });
    assert.deepEqual(await api.registerAPI('new_user', 'long-test-password', 'long-test-password'), {user});
    assert.equal(calls[0].url, 'http://local-test/auth/register');
    assert.equal(calls[0].headers['X-Xiaowan-User'], undefined);
    assert.deepEqual(JSON.parse(calls[0].body), {
        username: 'new_user', password: 'long-test-password', confirm_password: 'long-test-password'
    });
});

test('private requests identify the signed-in account and expose a definite pre-execution rejection', async t => {
    const calls = [];
    t.mock.method(globalThis, 'fetch', async (url, options) => {
        calls.push([url, options.headers['X-Xiaowan-User']]);
        if (url.endsWith('/chat')) {
            return new Response(JSON.stringify({detail: {code: 'CHAT_NOT_STARTED', message: '当前聊天较忙'}}), {status: 503});
        }
        return new Response(JSON.stringify({ok: true}));
    });
    api.invalidateRequests({id: 7, username: 'new_user', role: 'user'});
    try {
        await assert.rejects(api.chatAPI('消息'), error => error instanceof api.ChatNotStartedError && error.message === '当前聊天较忙');
        await api.logoutAPI();
    } finally {
        api.invalidateRequests();
    }
    assert.deepEqual(calls, [['http://local-test/chat', '7'], ['http://local-test/auth/logout', '7']]);
});

test('server validation of a chat message is a definite non-retryable rejection', async t => {
    t.mock.method(globalThis, 'fetch', async () => new Response(
        JSON.stringify({detail: {code: 'CHAT_NOT_STARTED', message: '聊天消息最多 8000 个字符'}}),
        {status: 422}
    ));
    await assert.rejects(api.chatAPI('消息'), error => error instanceof api.ChatNotStartedError &&
        error.retryable === false && error.message === '聊天消息最多 8000 个字符');
});

test('a missing personal model is a definite rejection that can be retried after setup', async t => {
    const message = '请先在设置中配置个人模型和 API Key';
    t.mock.method(globalThis, 'fetch', async () => new Response(
        JSON.stringify({detail: {code: 'CHAT_NOT_STARTED', message}}), {status: 403}
    ));
    await assert.rejects(api.chatAPI('消息'), error => error instanceof api.ChatNotStartedError &&
        error.retryable === true && error.message === message);
});

test('profile saves only nickname and system avatar ID; password change goes to its own endpoint', async t => {
    assert.equal(typeof api.profileAPI, 'function', 'profile API is missing');
    assert.equal(typeof api.changePasswordAPI, 'function', 'password API is missing');
    const calls = [];
    t.mock.method(globalThis, 'fetch', async (url, options) => {
        calls.push({url, method: options.method, body: options.body && JSON.parse(options.body)});
        return new Response(JSON.stringify({ok: true}));
    });
    await api.profileAPI();
    await api.profileAPI({nickname: '小林', avatar_id: 'fox'});
    await api.changePasswordAPI({current_password: 'old-password', new_password: 'new-password', confirm_password: 'new-password'});
    assert.deepEqual(calls, [
        {url: 'http://local-test/profile', method: 'GET', body: undefined},
        {url: 'http://local-test/profile', method: 'POST', body: {nickname: '小林', avatar_id: 'fox'}},
        {url: 'http://local-test/auth/password', method: 'POST', body: {current_password: 'old-password', new_password: 'new-password', confirm_password: 'new-password'}}
    ]);
});

test('settings are loaded/saved through backend and save failures are reported', async t => {
    const preferences = {send_shortcut: 'ctrl_enter', theme: 'theme2'};
    const calls = [];
    t.mock.method(globalThis, 'fetch', async (url, options) => {
        calls.push({url, method: options.method, body: options.body});
        return new Response(JSON.stringify({model: null, preferences}));
    });
    assert.deepEqual((await api.settingsAPI()).preferences, preferences);
    assert.deepEqual((await api.settingsAPI(preferences)).preferences, preferences);
    assert.equal(calls[0].method, 'GET');
    assert.equal(calls[1].method, 'POST');
    assert.equal(calls[1].url, 'http://local-test/settings');
    assert.deepEqual(JSON.parse(calls[1].body), preferences);
    globalThis.fetch = async () => new Response('{}', {status: 500});
    await assert.rejects(api.settingsAPI(preferences), /设置保存失败/);
});
