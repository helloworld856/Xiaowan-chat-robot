import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
import {runInNewContext} from 'node:vm';

test('logout requires confirmation and prevents relogin until its response completes', async () => {
    const source = fs.readFileSync(new URL('../src/ts/auth.ts', import.meta.url), 'utf8');
    const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
    const elements = new Map(), events = new Map();
    function element(id) {
        if (!elements.has(id)) elements.set(id, {
            hidden: true, inert: true, disabled: false, open: false, value: id === 'login-username' ? 'admin' : '', textContent: '',
            listeners: new Map(), classList: {add() {}, remove() {}}, focus() {}, setAttribute() {}, reportValidity: () => true,
            showModal() { this.open = true; }, close() { this.open = false; },
            addEventListener(name, handler) { this.listeners.set(name, handler); }
        });
        return elements.get(id);
    }
    let resolveLogout, logins = 0, logouts = 0;
    const pending = new Promise(resolve => { resolveLogout = resolve; });
    const api = {
        AuthRequiredError: class extends Error {}, invalidateRequests() {},
        sessionAPI: async () => ({user: {username: 'admin'}}),
        loginAPI: async () => { logins++; return {user: {username: 'admin'}}; }, logoutAPI: () => { logouts++; return pending; }
    };
    const exports = {};
    runInNewContext(compiled, {
        exports, require: name => name === './api.ts' ? api : {lockSendBtn() {}, showAlert() {}},
        document: {getElementById: element, querySelectorAll: () => [], querySelector: () => element('sidebar-button'), title: ''},
        window: {addEventListener: (name, handler) => events.set(name, handler)}
    });
    await exports.initializeAuth(async () => {}, () => {});
    assert.equal(element('app-shell').hidden, false);
    element('logout-btn').listeners.get('click')();
    assert.equal(logouts, 0, 'opening the confirmation must preserve the session');
    assert.equal(element('logout-dialog').open, true);
    await element('logout-cancel').listeners.get('click')();
    assert.equal(element('logout-dialog').open, false);
    assert.equal(element('app-shell').hidden, false);
    assert.equal(logouts, 0, 'cancelling must not request logout');
    await element('logout-confirm').listeners.get('click')();
    assert.equal(logouts, 0, 'a closed confirmation cannot request logout');
    await element('logout-btn').listeners.get('click')();
    const logout = element('logout-confirm').listeners.get('click')();
    assert.equal(logouts, 1);
    assert.equal(element('logout-dialog').open, false);
    events.get('auth-required')();
    assert.equal(element('login-screen').hidden, false);
    await element('login-form').listeners.get('submit')({preventDefault() {}});
    assert.equal(logins, 0, 'new login must wait for the old logout response');
    assert.equal(element('login-submit').disabled, true);
    resolveLogout({ok: true});
    await logout;
    assert.equal(element('login-submit').disabled, false);
    await element('login-form').listeners.get('submit')({preventDefault() {}});
    assert.equal(logins, 1);
    assert.equal(element('app-shell').hidden, false);

    events.get('password-change-start')();
    events.get('auth-required')();
    await element('login-form').listeners.get('submit')({preventDefault() {}});
    assert.equal(logins, 1, 'password-changing response must finish before relogin');
    assert.equal(element('login-submit').disabled, true);
    events.get('password-changed')();
    assert.equal(element('app-shell').hidden, true);
    assert.match(element('login-error').textContent, /密码已修改/);
    events.get('password-change-end')();
    assert.equal(element('login-submit').disabled, false);
});

test('signed-out startup shows the login form silently while network failure remains visible', async () => {
    const source = fs.readFileSync(new URL('../src/ts/auth.ts', import.meta.url), 'utf8');
    const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
    for (const offline of [false, true]) {
        const nodes = new Map();
        function element(id) {
            if (!nodes.has(id)) nodes.set(id, {
                hidden: true, inert: true, value: '', textContent: '',
                classList: {add() {}, remove() {}}, focus() {}, setAttribute() {}, addEventListener() {}
            });
            return nodes.get(id);
        }
        let initialized = 0;
        const exports = {};
        runInNewContext(compiled, {
            exports, require: name => name === './api.ts' ? {
                AuthRequiredError: class extends Error {}, invalidateRequests() {},
                sessionAPI: async () => { if (offline) throw new Error('offline'); return {user: null}; }
            } : {lockSendBtn() {}, showAlert() {}},
            document: {getElementById: element, querySelectorAll: () => [], querySelector: () => element('sidebar'), title: ''},
            window: {addEventListener() {}}
        });
        await exports.initializeAuth(async () => { initialized++; }, () => {});
        assert.equal(initialized, 0);
        assert.equal(element('login-screen').hidden, false);
        assert.equal(element('app-shell').hidden, true);
        if (offline) assert.match(element('login-error').textContent, /无法检查登录状态/);
        else assert.equal(element('login-error').textContent, '', 'being signed out is not a connection error');
    }
});
