import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
import {runInNewContext} from 'node:vm';

// 注入浏览器接口，直接执行入口模块的初始化逻辑。
function runModule(file, globals) {
    const source = fs.readFileSync(new URL('../src/ts/' + file, import.meta.url), 'utf8');
    const compiled = ts.transpileModule(source, {
        compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}
    }).outputText;
    return runInNewContext('(async () => {\n' + compiled + '\n})()', globals);
}

test('theme storage being unavailable does not prevent authentication', async () => {
    let authInitialized = false;
    const document = {documentElement: {className: ''}, getElementById: () => ({})};
    const globals = {
        exports: {},
        document,
        localStorage: {
            getItem() { throw new Error('Storage blocked'); },
            setItem() { throw new Error('Storage blocked'); }
        },
        console: {log() {}}
    };
    await runModule('utils.ts', {...globals, require: () => ({})});
    const utils = globals.exports;
    const defaultTheme = {value: 'theme0', checked: false};
    await runModule('main.ts', {...globals, exports: {}, require: name => ({
        './ui.ts': {lockSendBtn() {}, themeInput: [defaultTheme]},
        './utils.ts': utils,
        './addevent.ts': {addEventToUi() {}},
        './auth.ts': {async initializeAuth() { authInitialized = true; }}
    })[name]});
    assert.equal(authInitialized, true);
    assert.equal(document.documentElement.className, 'theme0');
    assert.equal(defaultTheme.checked, true);
});

test('loading the persona preserves the user avatar that their profile already supplied', async () => {
    const globals = {
        exports: {},
        document: {getElementById: () => ({}), querySelector: () => ({}), querySelectorAll: () => []},
        window: {USER_AVATAR: '/avatars/system/fox.png'}
    };
    await runModule('ui.ts', {...globals});
    globals.exports.updateUi({BOT_NAME: '小晚', BOT_AVATAR: '/avatars/xiaowan.png', USER_AVATAR: '/avatars/system/cat.png'});
    assert.equal(globals.window.USER_AVATAR, '/avatars/system/fox.png');
    assert.equal(globals.window.BOT_AVATAR, '/avatars/xiaowan.png');
});
