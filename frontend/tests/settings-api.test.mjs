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
    const preferences = {send_shortcut: 'ctrl_enter'};
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
