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

test('a failed profile read cannot save an uninitialized or cancelled draft', async () => {
    const source = fs.readFileSync(new URL('../src/ts/profile.ts', import.meta.url), 'utf8');
    const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
    const elements = new Map();
    const makeElement = () => ({
        value: '', disabled: false, hidden: false, open: false,
        classList: {toggle() {}, remove() {}}, listeners: new Map(),
        addEventListener(name, listener) { this.listeners.set(name, listener); },
        querySelectorAll() { return []; }, replaceChildren() {}, append() {},
        reset() {}, focus() {}, reportValidity() { return true; },
        showModal() { this.open = true; }, close() { this.open = false; }
    });
    const getElement = id => {
        if (!elements.has(id)) elements.set(id, makeElement());
        return elements.get(id);
    };
    const menu = makeElement();
    const posts = [];
    class AuthRequiredError extends Error {}
    class CancelledRequestError extends Error {}
    const exports = {};
    runInNewContext(compiled, {
        exports, window: {},
        document: {getElementById: getElement, querySelectorAll: () => []},
        require: name => name === './ui.ts' ? {menuBtn: makeElement(), menuSelect: [menu]} : {
            AuthRequiredError, CancelledRequestError,
            profileAPI: async payload => {
                if (!payload) throw new Error('GET failed');
                posts.push(payload);
                return payload;
            }
        }
    });
    exports.applyProfile({username: 'admin', nickname: 'Saved', avatar_id: 'fox', avatar_url: '/avatars/system/fox.png', avatars: [], default_avatar_id: 'cat'});
    exports.initProfile();
    await menu.listeners.get('click')();
    assert.equal(getElement('profile-fields').disabled, true);
    assert.equal(getElement('profile-save').disabled, true);
    getElement('profile-nickname').value = 'New nickname';
    await getElement('profile-form').listeners.get('submit')({preventDefault() {}});
    assert.equal(posts.length, 0);
});
