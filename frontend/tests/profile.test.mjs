import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
import {runInNewContext} from 'node:vm';

test('applying a saved profile updates the menu and all existing user avatars', () => {
    const source = fs.readFileSync(new URL('../src/ts/profile.ts', import.meta.url), 'utf8');
    const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
    const images = [{src: '/old.png'}, {src: '/old.png'}];
    const avatars = [{title: 'old'}, {title: 'old'}];
    const menu = {textContent: 'admin'};
    const userWindow = {USER_AVATAR: '/old.png', USER_NAME: 'admin'};
    const exports = {};
    runInNewContext(compiled, {
        exports, require: () => ({}), window: userWindow,
        document: {getElementById: () => menu, querySelectorAll: selector => selector === '.user_avatar img' ? images : avatars}
    });
    exports.applyProfile({username: 'admin', nickname: '小林', avatar_id: 'fox', avatar_url: '/avatars/system/fox.png', avatars: [], default_avatar_id: 'cat'});
    assert.equal(menu.textContent, '小林');
    assert.equal(userWindow.USER_NAME, '小林');
    assert.equal(userWindow.USER_AVATAR, '/avatars/system/fox.png');
    assert.deepEqual(images.map(image => image.src), ['/avatars/system/fox.png', '/avatars/system/fox.png']);
    assert.deepEqual(avatars.map(avatar => avatar.title), ['小林', '小林']);
});

function profileHarness(api, inputLimits) {
    const source = fs.readFileSync(new URL('../src/ts/profile.ts', import.meta.url), 'utf8');
    const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
    const elements = new Map();
    const makeElement = () => ({
        value: '', disabled: false, hidden: false, open: false,
        classList: {toggle() {}, remove() {}}, listeners: new Map(),
        addEventListener(name, listener) { this.listeners.set(name, listener); },
        querySelectorAll() { return []; }, replaceChildren() {}, append() {},
        reset() {}, focus() {}, setAttribute() {}, reportValidity() { return true; },
        showModal() { this.open = true; }, close() { this.open = false; }
    });
    const getElement = id => {
        if (!elements.has(id)) elements.set(id, makeElement());
        return elements.get(id);
    };
    const menu = makeElement();
    const posts = [];
    const events = [];
    const alerts = [];
    class AuthRequiredError extends Error {}
    class CancelledRequestError extends Error {}
    const exports = {};
    runInNewContext(compiled, {
        exports, window: {dispatchEvent(event) { events.push(event.type); }}, Event, Error,
        document: {getElementById: getElement, querySelectorAll: () => []},
        require: name => ({
            './ui.ts': {menuBtn: makeElement(), menuSelect: [menu], showAlert: message => alerts.push(message)},
            './confirm.ts': {confirmAction: api.confirmAction},
            './global_config.ts': {inputLimits},
            './api.ts': {
                AuthRequiredError, CancelledRequestError, getRequestGeneration: () => 0,
                canManageModel: () => api.admin === true,
                profileAPI: async payload => {
                    if (payload) posts.push(payload);
                    return api.profileAPI(payload);
                },
                changePasswordAPI: api.changePasswordAPI,
                deleteAccountAPI: api.deleteAccountAPI
            }
        })[name]
    });
    return {exports, getElement, menu, posts, events, alerts};
}

const savedProfile = {
    username: 'admin', nickname: 'Saved', avatar_id: 'fox', avatar_url: '/avatars/system/fox.png', avatars: [], default_avatar_id: 'cat'
};

test('account deletion requires confirmation and is unavailable to the built-in administrator', async () => {
    let confirmation = null;
    const passwords = [];
    const api = {
        profileAPI: async () => savedProfile,
        confirmAction: async (_, _description, _action, passwordRequired) => {
            assert.equal(passwordRequired, true);
            return confirmation;
        },
        deleteAccountAPI: async password => { passwords.push(password); }
    };
    const {exports, getElement, menu, events} = profileHarness(api);
    exports.initProfile();
    const button = getElement('profile-delete-account');
    const click = () => button.listeners.get('click')();
    api.admin = true;
    await menu.listeners.get('click')();
    assert.equal(button.disabled, true);
    await click();
    assert.deepEqual(passwords, []);
    getElement('profile-dialog').close();
    api.admin = false;
    await menu.listeners.get('click')();
    await click();
    assert.deepEqual(passwords, []);
    confirmation = 'current-test-password';
    await click();
    assert.deepEqual(passwords, [confirmation]);
    assert.deepEqual(events, ['account-deleted']);
});

// 保存前需要真实资料，网络错误与取消都不能让空表单提交。
test('a failed profile read cannot save an uninitialized or cancelled draft', async () => {
    const {exports, getElement, menu, posts} = profileHarness({
        profileAPI: async () => { throw new Error('GET failed'); }
    });
    exports.applyProfile(savedProfile);
    exports.initProfile();
    await menu.listeners.get('click')();
    assert.equal(getElement('profile-fields').disabled, true);
    assert.equal(getElement('profile-save').disabled, true);
    getElement('profile-nickname').value = 'New nickname';
    await getElement('profile-form').listeners.get('submit')({preventDefault() {}});
    assert.equal(posts.length, 0);
});

test('a configured nickname limit counts Unicode characters', async () => {
    const {exports, getElement, menu, posts, alerts} = profileHarness({
        profileAPI: async payload => ({...savedProfile, ...payload})
    }, {max_nickname_chars: 3});
    exports.initProfile();
    await menu.listeners.get('click')();
    const nickname = getElement('profile-nickname');
    const submit = () => getElement('profile-form').listeners.get('submit')({preventDefault() {}});
    nickname.value = '🙂🙂🙂🙂';
    await submit();
    assert.equal(posts.length, 0);
    assert.match(alerts.at(-1), /3/);
    nickname.value = '🙂🙂🙂';
    await submit();
    assert.equal(posts[0].nickname, '🙂🙂🙂');
});

test('profile save shows its result and keeps edits available after a failed request', async () => {
    let fail = true;
    const {exports, getElement, menu, alerts} = profileHarness({
        profileAPI: async payload => {
            if (!payload) return savedProfile;
            if (fail) throw new Error('保存个人资料失败');
            return {...savedProfile, ...payload};
        }
    }, {max_nickname_chars: 32});
    exports.initProfile();
    await menu.listeners.get('click')();
    const nickname = getElement('profile-nickname');
    const submit = () => getElement('profile-form').listeners.get('submit')({preventDefault() {}});
    nickname.value = 'New name';
    await submit();
    assert.deepEqual(alerts, ['保存个人资料失败']);
    assert.equal(nickname.value, 'New name');
    assert.equal(getElement('profile-dialog').open, true);
    assert.equal(getElement('profile-save').disabled, false);

    fail = false;
    await submit();
    assert.equal(alerts.at(-1), '个人资料已保存。');
    assert.equal(getElement('profile-dialog').open, false);
    assert.equal(getElement('current-username').textContent, 'New name');
});

test('changing a password enforces the configured length range', async () => {
    const changes = [];
    const {exports, getElement} = profileHarness({
        changePasswordAPI: async payload => { changes.push(payload); return {ok: true}; }
    }, {password_min_length: 8, password_max_length: 20});
    exports.initProfile();
    getElement('profile-current-password').value = 'Old!12';
    const submit = async value => {
        getElement('profile-new-password').value = value;
        getElement('profile-confirm-password').value = value;
        await getElement('profile-password-form').listeners.get('submit')({preventDefault() {}});
    };
    await submit('Short!1');
    await submit('🙂'.repeat(21));
    assert.equal(changes.length, 0);
    assert.match(getElement('profile-password-status').textContent, /8–20/);
    const valid = '🙂'.repeat(20);
    await submit(valid);
    assert.equal(changes[0].new_password, valid);
    assert.equal(changes[0].current_password, 'Old!12');
});
