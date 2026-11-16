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

test('a delayed settings response does not replace a model that the admin just switched to', async () => {
    const nodes = new Map();
    function element(id) {
        if (!nodes.has(id)) nodes.set(id, {
            value: '', disabled: false, listeners: new Map(), replaceChildren() {}, querySelectorAll: () => [],
            reportValidity: () => true,
            addEventListener(name, handler) { this.listeners.set(name, handler); }
        });
        return nodes.get(id);
    }
    const before = {model_merchant: 'deepseek', model_name: 'deepseek-v4-flash', base_url: ''};
    const modelConfig = {model_valid: true, model: before};
    const preferences = {send_shortcut: 'enter'};
    let finishSettings;
    const exports = {};
    runInNewContext(compiled, {
        exports,
        require: name => ({
            './api.ts': {
                canManageModel: () => true,
                getRequestGeneration: () => 1,
                modelAPI: async () => ({status: true, info: ''}),
                settingsAPI: () => new Promise(resolve => { finishSettings = resolve; })
            },
            './global_config.ts': {modelConfig},
            './preferences.ts': {preferences},
            './ui.ts': {openPopup() {}, closePopup: async () => true, menuSelect: [null, element('settings')]}
        })[name],
        document: {getElementById: element, querySelector: element}
    });
    exports.initSettings();
    const settings = exports.loadSettings();
    element('modelSelect').value = 'tongyi';
    element('modelName').value = 'qwen-plus';
    element('model-config-form').listeners.get('submit')({preventDefault() {}});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(element('display-model').textContent, 'qwen-plus');
    finishSettings({model: before, model_ready: true, preferences: {send_shortcut: 'ctrl_enter'}});
    await settings;
    assert.equal(element('display-model').textContent, 'qwen-plus');
    assert.equal(element('send-shortcut').value, 'ctrl_enter');
});
