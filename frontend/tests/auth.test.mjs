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

test('registration enforces the session password range and returns to login', async () => {
    const source = fs.readFileSync(new URL('../src/ts/auth.ts', import.meta.url), 'utf8');
    const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
    const nodes = new Map();
    const attempts = [];
    const user = {id: 8, username: 'new_account', role: 'user'};
    function element(id) {
        if (!nodes.has(id)) nodes.set(id, {
            hidden: true, inert: true, value: '', textContent: '', listeners: new Map(),
            focus() {}, setAttribute() {}, classList: {add() {}}, reportValidity: () => true,
            addEventListener(name, handler) { this.listeners.set(name, handler); }
        });
        return nodes.get(id);
    }
    const exports = {};
    const limits = {password_min_length: 8, password_max_length: 20, max_nickname_chars: 40, max_message_chars: 9000};
    runInNewContext(compiled, {
        exports, require: name => ({
            './api.ts': {
                invalidateRequests() {}, sessionAPI: async () => ({user: null, limits}),
                registerAPI: async (...args) => { attempts.push(args); return {user}; }
            },
            './global_config.ts': {inputLimits: {password_min_length: 6, password_max_length: 15}},
            './ui.ts': {lockSendBtn() {}, showAlert() {}}
        })[name],
        document: {getElementById: element, querySelectorAll: () => [], querySelector: () => element('sidebar'), title: ''},
        window: {addEventListener() {}}
    });
    await exports.initializeAuth(async () => assert.fail('registration must not sign in'), () => {});
    element('open-register').listeners.get('click')();
    assert.equal(element('register-form').hidden, false);
    element('register-username').value = user.username;
    const submit = () => element('register-form').listeners.get('submit')({preventDefault() {}});
    element('register-password').value = 'Reg!12';
    element('register-confirm-password').value = 'Reg!12';
    await submit();
    assert.equal(attempts.length, 0);
    assert.match(element('register-error').textContent, /8–20/);
    const valid = '🙂'.repeat(20);
    element('register-password').value = valid;
    element('register-confirm-password').value = 'Wrong!12';
    await submit();
    assert.equal(attempts.length, 0);
    assert.match(element('register-error').textContent, /密码不一致/);
    element('register-confirm-password').value = valid;
    await submit();
    assert.deepEqual(attempts, [[user.username, valid, valid]]);
    assert.equal(element('app-shell').hidden, true);
    assert.equal(element('register-form').hidden, true);
    assert.equal(element('login-username').value, user.username);
    assert.equal(element('register-password').value, '');
});

// 用两个标签页模拟会话变化与页面启动响应的先后顺序。
test('an auth change in another tab clears private state and rejects a stale startup response', async () => {
    const source = fs.readFileSync(new URL('../src/ts/auth.ts', import.meta.url), 'utf8');
    const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
    const alice = {id: 8, username: 'alice', role: 'user'};
    const tabs = [];

    function tab(sessionAPI) {
        const nodes = new Map(), listeners = new Map();
        const state = {sitsIn: 0, resets: 0, discarded: 0};
        function element(id) {
            if (!nodes.has(id)) nodes.set(id, {
                hidden: true, inert: true, value: '', textContent: '', listeners: new Map(),
                focus() {}, setAttribute() {}, classList: {add() {}}, reportValidity: () => true,
                showModal() { this.open = true; }, close() { this.open = false; },
                addEventListener(name, handler) { this.listeners.set(name, handler); }
            });
            return nodes.get(id);
        }
        const exports = {};
        const localStorage = {
            setItem(key, newValue) {
                for (const other of tabs) {
                    if (other.storage !== this) other.listeners.get('storage')?.({key, newValue, storageArea: other.storage});
                }
            }
        };
        runInNewContext(compiled, {
            exports, localStorage, require: name => name === './api.ts' ? {
                invalidateRequests(user) { if (!user) state.discarded++; }, sessionAPI,
                loginAPI: async () => ({user: alice}), logoutAPI: async () => ({ok: true})
            } : {lockSendBtn() {}, showAlert() {}},
            document: {getElementById: element, querySelectorAll: () => [], querySelector: () => element('sidebar'), title: ''},
            window: {addEventListener: (name, handler) => listeners.set(name, handler)}
        });
        const ctx = {element, listeners, state, storage: localStorage};
        tabs.push(ctx);
        return {...ctx, initialized: exports.initializeAuth(async () => { state.sitsIn++; }, () => { state.resets++; })};
    }

    const active = tab(async () => ({user: alice}));
    await active.initialized;
    let finishSession;
    const startedEarlier = tab(() => new Promise(resolve => { finishSession = resolve; }));
    const trigger = tab(async () => ({user: alice}));
    await trigger.initialized;
    assert.equal(active.element('app-shell').hidden, false);
    active.element('login-screen').hidden = true;
    trigger.element('logout-btn').listeners.get('click')();
    await trigger.element('logout-confirm').listeners.get('click')();
    assert.equal(active.element('app-shell').hidden, true);
    assert.equal(active.state.resets, 1);
    assert.equal(active.state.discarded, 1);
    assert.equal(startedEarlier.element('login-screen').hidden, true);
    finishSession({user: alice});
    await startedEarlier.initialized;
    assert.equal(startedEarlier.state.sitsIn, 0);
    assert.equal(startedEarlier.element('app-shell').hidden, true);
    assert.equal(startedEarlier.element('login-screen').hidden, false);

    let restoredUser = alice;
    const cached = tab(async () => ({user: restoredUser}));
    await cached.initialized;
    cached.listeners.get('pagehide')?.({persisted: true});
    assert.equal(cached.element('app-shell').hidden, true, 'cached page must hide private content before navigation');
    await cached.listeners.get('pageshow')({persisted: true});
    assert.equal(cached.element('app-shell').hidden, false);
    cached.listeners.get('pagehide')({persisted: true});
    restoredUser = {id: 9, username: 'bob', role: 'user'};
    await cached.listeners.get('pageshow')({persisted: true});
    assert.equal(cached.element('app-shell').hidden, true);
    assert.equal(cached.element('login-screen').hidden, false);
    assert.equal(cached.state.resets, 1);
});
