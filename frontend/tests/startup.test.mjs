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

test('startup does not read or write browser theme preferences', async () => {
    let authInitialized = false;
    let storageAccesses = 0;
    const document = {documentElement: {className: ''}, getElementById: () => ({})};
    const globals = {
        exports: {},
        document,
        localStorage: {
            getItem() { storageAccesses++; throw new Error('Preferences must come from the account'); },
            setItem() { storageAccesses++; throw new Error('Preferences must go to the account'); }
        },
        console: {log() {}}
    };
    await runModule('utils.ts', {...globals, require: () => ({})});
    const utils = globals.exports;
    await runModule('main.ts', {...globals, exports: {}, require: name => ({
        './ui.ts': {lockSendBtn() {}},
        './utils.ts': utils,
        './addevent.ts': {addEventToUi() {}},
        './auth.ts': {async initializeAuth() { authInitialized = true; }}
    })[name]});
    assert.equal(authInitialized, true);
    assert.equal(storageAccesses, 0);
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
