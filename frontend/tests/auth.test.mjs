import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
import {runInNewContext} from 'node:vm';
import ts from 'typescript';
import {mountComponent, nextTick} from './vue-harness.mjs';

const require = createRequire(import.meta.url);
const alice = {id: 8, username: 'alice', role: 'user'};
const settle = () => new Promise(resolve => setImmediate(resolve));

function tab({api = {}, tabs = [], confirmAction = async () => ''} = {}) {
    const source = fs.readFileSync(new URL('../src/composables/useSession.ts', import.meta.url), 'utf8');
    const compiled = ts.transpileModule(source, {
        compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}
    }).outputText;
    const vue = require('vue');
    const renderer = vue.createRenderer({createComment: () => ({}), insert() {}, remove() {}});
    const events = new EventTarget();
    const emit = (type, data = {}) => events.dispatchEvent(Object.assign(new Event(type), data));
    const localStorage = {
        setItem(key, newValue) {
            for (const other of tabs) {
                if (other !== emit) other('storage', {key, newValue});
            }
        }
    };
    tabs.push(emit);
    const state = {authenticated: [], signedOut: 0, invalidated: [], alerts: []};
    const exports = {};
    runInNewContext(compiled, {
        exports, window: events, localStorage,
        require: name => ({
            vue,
            '../ts/api.ts': {
                AuthRequiredError: class extends Error {},
                invalidateRequests: user => state.invalidated.push(user),
                sessionAPI: async () => ({user: null}),
                ...api
            },
            '../ts/global_config.ts': {inputLimits: {password_min_length: 6, password_max_length: 15}}
        })[name]
    });
    let session;
    const app = renderer.createApp({
        setup() {
            session = exports.useSession({
                onAuthenticated: async user => { state.authenticated.push(user); },
                onSignedOut: () => { state.signedOut++; },
                showAlert: message => { state.alerts.push(message); },
                confirmAction
            });
            return () => null;
        }
    });
    app.mount({});
    return {session, state, emit, unmount: () => app.unmount()};
}

test('logout requires confirmation and blocks login until its cookie mutation settles', async () => {
    let confirmation, resolveLogout, logins = 0, logouts = 0;
    const pending = new Promise(resolve => { resolveLogout = resolve; });
    const {session, emit} = tab({
        api: {
            sessionAPI: async () => ({user: alice}),
            loginAPI: async () => { logins++; return {user: alice}; },
            logoutAPI: () => { logouts++; return pending; }
        },
        confirmAction: async options => {
            assert.equal(options.variant, 'logout');
            return new Promise(resolve => { confirmation = resolve; });
        }
    });
    await settle();
    assert.equal(session.user.value, alice);
    const cancelled = session.logout();
    assert.equal(logouts, 0);
    confirmation(null);
    await cancelled;
    assert.equal(logouts, 0);
    assert.equal(session.user.value, alice);

    const logout = session.logout();
    confirmation('');
    await settle();
    assert.equal(logouts, 1);
    assert.equal(session.loggingOut.value, true);
    emit('auth-required');
    assert.equal(session.user.value, null);
    await session.login();
    assert.equal(logins, 0, 'new login must wait for the old logout response');
    assert.equal(session.busy.value, true);
    resolveLogout({ok: true});
    await logout;
    assert.equal(session.busy.value, false);
    session.username.value = 'alice';
    session.password.value = 'example password';
    await session.login();
    assert.equal(logins, 1);
    assert.equal(session.user.value, alice);
    assert.equal(session.password.value, '');
});

test('credential events retain the mutation lock until completion and clear deleted login names', async () => {
    let logins = 0;
    const {session, emit} = tab({
        api: {
            sessionAPI: async () => ({user: alice}),
            loginAPI: async () => { logins++; return {user: alice}; }
        }
    });
    await settle();
    session.username.value = 'alice';
    emit('password-change-start');
    emit('auth-required');
    await session.login();
    assert.equal(logins, 0);
    assert.equal(session.busy.value, true);
    emit('password-changed');
    assert.equal(session.user.value, null);
    assert.match(session.loginMessage.value, /密码已修改/);
    assert.equal(session.loginTone.value, 'success');
    assert.equal(session.busy.value, true);
    emit('password-change-end');
    assert.equal(session.busy.value, false);
    assert.equal(session.username.value, 'alice');

    await session.login();
    emit('account-deleted');
    assert.equal(session.user.value, null);
    assert.equal(session.username.value, '');
    assert.match(session.loginMessage.value, /账户已注销/);
    assert.equal(session.loginTone.value, 'success');
});

test('signed-out startup is silent and startup network failure remains visible', async () => {
    for (const offline of [false, true]) {
        const {session, state} = tab({api: {
            sessionAPI: async () => {
                if (offline) throw new Error('offline');
                return {user: null};
            }
        }});
        assert.equal(session.starting.value, true);
        await settle();
        assert.equal(session.starting.value, false);
        assert.equal(session.user.value, null);
        assert.deepEqual(state.authenticated, []);
        if (offline) assert.match(session.loginMessage.value, /无法检查登录状态/);
        else assert.equal(session.loginMessage.value, '');
    }
});

test('registration enforces the session password range and returns to login', async () => {
    const attempts = [];
    const newUser = {id: 9, username: 'new_account', role: 'user'};
    const limits = {password_min_length: 8, password_max_length: 20, max_nickname_chars: 40, max_message_chars: 9000};
    const {session, state} = tab({api: {
        sessionAPI: async () => ({user: null, limits}),
        registerAPI: async (...args) => { attempts.push(args); return {user: newUser}; }
    }});
    await settle();
    assert.equal(session.switchForm(true), true);
    session.registerUsername.value = 'Admin';
    assert.equal(await session.register(), 'username');
    assert.match(session.registrationMessage.value, /保留用户名/);

    session.registerUsername.value = ' new_account ';
    session.registerPassword.value = 'Reg!12';
    session.registerConfirmation.value = 'Reg!12';
    assert.equal(await session.register(), 'password');
    assert.match(session.registrationMessage.value, /8–20/);
    const valid = '🙂'.repeat(20);
    session.registerPassword.value = valid;
    session.registerConfirmation.value = 'Wrong!12';
    assert.equal(await session.register(), 'confirmation');
    assert.match(session.registrationMessage.value, /密码不一致/);
    assert.deepEqual(attempts, []);

    session.registerConfirmation.value = valid;
    assert.equal(await session.register(), 'registered');
    assert.deepEqual(attempts, [[newUser.username, valid, valid]]);
    assert.deepEqual(state.authenticated, []);
    assert.equal(session.user.value, null);
    assert.equal(session.registrationOpen.value, false);
    assert.equal(session.username.value, newUser.username);
    assert.equal(session.registerPassword.value, '');
    assert.equal(session.registerConfirmation.value, '');
    assert.equal(session.loginTone.value, 'success');
});

test('another tab clears private state without reopening stale startup results', async () => {
    const tabs = [];
    const active = tab({tabs, api: {sessionAPI: async () => ({user: alice})}});
    await settle();
    let resolveSession;
    const earlier = tab({tabs, api: {sessionAPI: () => new Promise(resolve => { resolveSession = resolve; })}});
    const trigger = tab({tabs, api: {
        sessionAPI: async () => ({user: alice}),
        logoutAPI: async () => ({ok: true})
    }});
    const signedOut = tab({tabs});
    await settle();
    signedOut.session.switchForm(true);
    signedOut.session.registerUsername.value = 'unfinished';
    assert.equal(active.session.user.value, alice);
    await trigger.session.logout();
    assert.equal(active.session.user.value, null);
    assert.equal(active.state.signedOut, 1);
    assert.deepEqual(active.state.invalidated, [alice, undefined]);
    assert.equal(signedOut.session.registrationOpen.value, true);
    assert.equal(signedOut.session.registerUsername.value, 'unfinished');
    assert.equal(earlier.session.starting.value, true);
    resolveSession({user: alice});
    await settle();
    assert.deepEqual(earlier.state.authenticated, []);
    assert.equal(earlier.session.user.value, null);
    assert.equal(earlier.session.starting.value, false);
    assert.match(earlier.session.loginMessage.value, /登录状态已更改/);
});

test('back-forward cache keeps the account hidden until the cookie is revalidated', async () => {
    let restoredUser = alice;
    const cached = tab({api: {sessionAPI: async () => ({user: restoredUser})}});
    await settle();
    cached.emit('pagehide', {persisted: true});
    assert.equal(cached.session.sessionHidden.value, true);
    assert.equal(cached.session.user.value, alice);
    cached.emit('pageshow', {persisted: true});
    assert.equal(cached.session.sessionHidden.value, true);
    await settle();
    assert.equal(cached.session.sessionHidden.value, false);
    cached.emit('pagehide', {persisted: true});
    restoredUser = {...alice, role: 'admin'};
    cached.emit('pageshow', {persisted: true});
    await settle();
    assert.equal(cached.session.user.value, null);
    assert.equal(cached.state.signedOut, 1);
});

test('a logout confirmation for an expired account cannot log out a newer session', async () => {
    let confirmation, logouts = 0;
    const aliceAgain = {...alice};
    const {session, state, emit, unmount} = tab({
        api: {
            sessionAPI: async () => ({user: alice}),
            loginAPI: async () => ({user: aliceAgain}),
            logoutAPI: async () => { logouts++; return {ok: true}; }
        },
        confirmAction: () => new Promise(resolve => { confirmation = resolve; })
    });
    await settle();
    const stale = session.logout();
    emit('auth-required');
    await session.login();
    confirmation('');
    await stale;
    assert.equal(logouts, 0);
    assert.equal(session.user.value, aliceAgain);
    unmount();
    emit('auth-required');
    assert.equal(state.signedOut, 1);
});

test('the auth screen moves focus between forms and submits passwords filled directly into the DOM', async t => {
    const doc = {title: ''};
    globalThis.document = doc;
    t.after(() => { delete globalThis.document; });
    const newUser = {id: 9, username: 'new_account', role: 'user'};
    const logins = [];
    const {session, unmount} = tab({api: {
        registerAPI: async () => ({user: newUser}),
        loginAPI: async (...args) => { logins.push(args); return {user: newUser}; }
    }});
    t.after(unmount);
    await settle();
    const screen = mountComponent(new URL('../src/components/AuthScreen.vue', import.meta.url), {props: {session}});
    t.after(screen.unmount);
    assert.equal(screen.element('login-username').focused, true);
    assert.equal(doc.title, '登录 · 小晚');

    await screen.act('open-register');
    assert.equal(screen.element('login-form').props.hidden, true);
    assert.equal(screen.element('register-form').props.hidden, false);
    assert.equal(screen.element('register-username').focused, true);
    assert.equal(doc.title, '注册 · 小晚');
    screen.input('register-username', newUser.username);
    screen.element('register-password').value = 'new password';
    screen.element('register-confirm-password').value = 'new password';
    await screen.act('register-form', 'submit');
    assert.equal(screen.element('register-form').props.hidden, true);
    assert.equal(screen.element('login-username').value, newUser.username);
    assert.equal(screen.element('login-password').focused, true);
    assert.equal(screen.element('login-error').props['data-tone'], 'success');

    screen.element('login-password').value = 'new password';
    await screen.act('login-form', 'submit');
    await nextTick();
    assert.deepEqual(logins, [[newUser.username, 'new password']]);
    assert.equal(screen.element('login-password').value, '');
});
