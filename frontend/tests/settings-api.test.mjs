import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';

globalThis.window = {location: {origin: 'http://local-test'}};
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
