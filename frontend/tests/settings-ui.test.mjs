import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
import {runInNewContext} from 'node:vm';

const source = fs.readFileSync(new URL('../src/ts/settings.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;

test('only the relevant model fields are visible and active for each role', () => {
    let admin = true;
    const nodes = new Map();
    function element(id) {
        if (!nodes.has(id)) nodes.set(id, {});
        return nodes.get(id);
    }
    const exports = {};
    runInNewContext(compiled, {
        exports,
        require: () => ({canManageModel: () => admin}),
        document: {getElementById: element, querySelectorAll: selector => [element(selector)]}
    });

    for (admin of [true, false]) {
        exports.updateSettingsPermissions();
        assert.equal(element('[data-model-admin]').hidden, !admin);
        assert.equal(element('[data-model-personal]').hidden, admin);
        assert.equal(element('model-base-url').disabled, !admin);
        assert.equal(element('model-api-key').disabled, admin);
        assert.equal(element('model-api-key').required, !admin);
    }
});

// 改变异步返回顺序，验证先完成的模型切换不会被旧读取覆盖。
test('preferences auto-save the latest selections in order and restore saved values on failure', async () => {
    const nodes = new Map();
    function element(id) {
        if (!nodes.has(id)) nodes.set(id, {
            value: '', disabled: false, listeners: new Map(), reset() {}, replaceChildren() {}, querySelectorAll: () => [],
            reportValidity: () => true,
            addEventListener(name, handler) { this.listeners.set(name, handler); }
        });
        return nodes.get(id);
    }
    const before = {model_merchant: 'deepseek', model_name: 'deepseek-v4-flash', base_url: ''};
    const modelConfig = {model_valid: true, model: before};
    const preferences = {send_shortcut: 'enter', theme: 'theme0'};
    const themeRadio = element('theme-radio');
    themeRadio.value = 'theme2';
    themeRadio.checked = true;
    const documentElement = {className: 'theme0'};
    const themeUtils = {};
    const utils = fs.readFileSync(new URL('../src/ts/utils.ts', import.meta.url), 'utf8');
    runInNewContext(ts.transpileModule(utils, {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText, {
        exports: themeUtils, require: () => ({}), document: {documentElement},
        localStorage: {setItem() { throw new Error('Preferences must not use browser storage'); }}
    });
    let finishSettings;
    const saves = [];
    const exports = {};
    runInNewContext(compiled, {
        exports,
        Error,
        require: name => ({
            './api.ts': {
                canManageModel: () => true,
                getRequestGeneration: () => 1,
                modelAPI: async () => ({status: true, info: ''}),
                settingsAPI: value => {
                    if (value) return new Promise((resolve, reject) => {
                        saves.push({
                            preferences: {...value},
                            finish: () => resolve({model: modelConfig.model, model_ready: true, preferences: {...value}}),
                            reject
                        });
                    });
                    return new Promise(resolve => { finishSettings = resolve; });
                }
            },
            './global_config.ts': {modelConfig},
            './preferences.ts': {preferences},
            './ui.ts': {openPopup() {}, resetPopup() {}, closePopup: async () => true, menuSelect: [null, element('settings')],
                themeInput: [themeRadio]},
            './utils.ts': themeUtils
        })[name],
        document: {getElementById: element, querySelector: element, querySelectorAll: () => []}
    });
    exports.initSettings();
    const settings = exports.loadSettings();
    element('modelSelect').value = 'tongyi';
    element('modelName').value = 'qwen-plus';
    element('model-config-form').listeners.get('submit')({preventDefault() {}});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(element('display-model').textContent, 'qwen-plus');
    finishSettings({model: before, model_ready: true, preferences: {send_shortcut: 'ctrl_enter', theme: 'theme3'}});
    await settings;
    assert.equal(element('display-model').textContent, 'qwen-plus');
    assert.equal(element('send-shortcut').value, 'ctrl_enter');
    assert.equal(documentElement.className, 'theme3');
    assert.equal(themeRadio.checked, false);
    themeRadio.checked = true;
    themeRadio.listeners.get('change')();
    assert.equal(documentElement.className, 'theme2');
    assert.deepEqual(saves.map(save => save.preferences), [{theme: 'theme2', send_shortcut: 'ctrl_enter'}]);
    const shortcut = element('send-shortcut');
    shortcut.value = 'enter';
    shortcut.listeners.get('change')();
    assert.equal(saves.length, 1);
    saves[0].finish();
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(saves[1].preferences, {theme: 'theme2', send_shortcut: 'enter'});
    assert.equal(shortcut.value, 'enter');
    saves[1].finish();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(preferences.theme, 'theme2');
    assert.equal(preferences.send_shortcut, 'enter');

    shortcut.value = 'ctrl_enter';
    shortcut.listeners.get('change')();
    saves[2].reject(new Error('测试保存失败'));
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(shortcut.value, 'enter');
    assert.equal(documentElement.className, 'theme2');
    assert.equal(element('preferences-status').textContent, '测试保存失败');
    exports.resetSettings();
    assert.equal(documentElement.className, 'theme0');
});
