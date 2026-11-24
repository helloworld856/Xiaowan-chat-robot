import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {compileModule, mountComponent, nextTick as tick} from './vue-harness.mjs';

const file = new URL('../src/components/SettingsDialog.vue', import.meta.url);
const dialogs = compileModule(readFileSync(new URL('../src/ts/dialogs.ts', import.meta.url), 'utf8'));
const defaults = {send_shortcut: 'enter', theme: 'theme0'};

function mount(api = {}, props = {}) {
    const preferences = {...defaults};
    const alerts = [];
    return {
        ...mountComponent(file, {
            imports: {'../ts/api.ts': {getRequestGeneration: () => 1, ...api}, '../ts/dialogs.ts': dialogs},
            props: {
                isAdmin: true, chatBusy: false, preferences, showAlert: message => alerts.push(message),
                confirmAction: async () => null, onPreferencesChange: next => Object.assign(preferences, next), ...props
            }
        }),
        preferences,
        alerts
    };
}

const catalog = {
    default_provider: 'tongyi',
    providers: {
        tongyi: {
            default_model: 'server-selected-model', default_address: 'https://default.example/v1',
            names: ['server-selected-model', 'saved-model'], saved_model: 'server-selected-model',
            saved_address: 'https://saved-address.example/v1', has_saved_key: true
        },
        deepseek: {
            default_model: 'new-model', default_address: 'https://deepseek.example/v1',
            names: ['new-model'], saved_model: null, saved_address: null, has_saved_key: false
        }
    }
};

function saved(preferences = defaults, model = null) {
    return {preferences, model, model_ready: Boolean(model), model_options: structuredClone(catalog)};
}

const pending = () => Promise.withResolvers();

test('preference drafts apply only after a successful save and reopen from the committed values', async () => {
    const saves = [];
    const loaded = {send_shortcut: 'ctrl_enter', theme: 'theme3'};
    const {element, act, select, session, preferences, alerts} = mount({
        settingsAPI: value => {
            if (!value) return Promise.resolve(saved(loaded));
            const request = pending();
            saves.push({...request, value: {...value}});
            return request.promise;
        }
    });
    await session.load();
    session.open();
    await tick();
    assert.deepEqual(preferences, loaded);
    assert.equal(element('theme-select').value, 'theme3');

    select('theme-select', 'theme2');
    select('send-shortcut', 'enter');
    assert.equal(saves.length, 0);
    assert.deepEqual(preferences, loaded);

    const submitted = act('preferences-form', 'submit');
    await tick();
    assert.equal(element('preferences-fields').props.disabled, true);
    assert.deepEqual(saves.map(save => save.value), [{theme: 'theme2', send_shortcut: 'enter'}]);
    await act('preferences-form', 'submit');
    assert.equal(saves.length, 1);
    saves[0].resolve(saved(saves[0].value));
    await submitted;
    assert.deepEqual(preferences, saves[0].value);
    assert.match(alerts.at(-1), /保存成功/);

    select('theme-select', 'theme1');
    select('send-shortcut', 'ctrl_enter');
    const failed = act('preferences-form', 'submit');
    saves[1].reject(new Error('测试保存失败'));
    await failed;
    assert.equal(element('theme-select').value, 'theme1');
    assert.equal(element('send-shortcut').value, 'ctrl_enter');
    assert.deepEqual(preferences, saves[0].value);
    assert.match(alerts.at(-1), /保存失败/);

    await act('setting-container', 'cancel');
    session.open();
    await tick();
    assert.equal(element('theme-select').value, 'theme2');
    assert.equal(element('send-shortcut').value, 'enter');
    session.reset();
    await tick();
    assert.equal(element('setting-container').open, false);
    assert.equal(element('theme-select').value, 'theme0');
    assert.equal(element('preferences-fields').props.disabled, true);
});

test('a late settings read can apply preferences without undoing a model switch', async () => {
    let next = Promise.resolve(saved());
    const {element, act, session, preferences} = mount({
        settingsAPI: () => next,
        modelAPI: async () => ({status: true})
    });
    await session.load();
    const delayed = pending();
    next = delayed.promise;
    const loading = session.load();

    await act('open-modify-btn');
    assert.equal(element('modelSelect').value, 'tongyi');
    assert.equal(element('modelName').value, 'server-selected-model');
    await act('model-config-form', 'submit');
    assert.equal(element('display-model').text, 'server-selected-model');
    assert.equal(element('display-model-status').text, '可用');

    delayed.resolve(saved({send_shortcut: 'enter', theme: 'theme1'},
        {model_merchant: 'deepseek', model_name: 'new-model'}));
    await loading;
    assert.equal(element('display-model').text, 'server-selected-model');
    assert.equal(preferences.theme, 'theme1');
});

test('the model editor masks API keys, clears autofill, and preserves provider address semantics', async () => {
    const calls = [];
    const {element, act, select, session, update} = mount({
        settingsAPI: async () => saved(),
        modelAPI: async (model, testOnly, key, personal) => {
            calls.push({...model, testOnly, key, personal});
            return {status: true, info: '验证通过'};
        }
    });
    await session.load();
    await act('open-modify-btn');
    const key = element('model-api-key');
    const toggle = element('toggle-model-key');
    const address = element('model-base-url');
    assert.equal(key.props.type, 'password');
    assert.equal(key.props.autocomplete, 'off');
    assert.equal(toggle.props.type, 'button');
    assert.equal(toggle.props['aria-controls'], 'model-api-key');
    assert.equal(key.props.required, false);
    assert.equal(key.props.placeholder, '********');
    assert.equal(key.value, '');
    assert.match(element('model-address-help').text, /服务器批准/);
    assert.equal(address.value, 'https://saved-address.example/v1');
    assert.deepEqual(element('modelName').options.map(option => option.value), catalog.providers.tongyi.names);

    key.value = 'browser-login-password';
    await act('toggle-model-key');
    assert.equal(key.props.type, 'password');
    assert.equal(key.value, '');
    key.value = 'browser-login-password';
    await act('test-model-btn');
    assert.deepEqual(calls.at(-1), {
        model_merchant: 'tongyi', model_name: 'server-selected-model',
        base_url: undefined, key: undefined, testOnly: true, personal: false
    });
    assert.equal(element('display-model-status').text, '未就绪');

    update({isAdmin: false});
    await act('open-modify-btn');
    assert.equal(element('open-modify-btn').text, '配置个人模型');
    assert.equal(element('submit-model-btn').text, '保存模型');
    assert.match(element('model-address-help').text, /公网 HTTPS/);
    const change = () => act('model-base-url', 'input');
    address.value = catalog.providers.tongyi.default_address;
    change();
    await act('test-model-btn');
    assert.deepEqual(calls.at(-1), {
        model_merchant: 'tongyi', model_name: 'server-selected-model',
        base_url: '', key: undefined, testOnly: true, personal: true
    });

    select('modelSelect', 'deepseek');
    await tick();
    assert.equal(key.props.required, true);
    assert.equal(key.props.placeholder, '');
    assert.equal(address.value, catalog.providers.deepseek.default_address);
    key.value = 'browser-login-password';
    await act('test-model-btn');
    assert.equal(calls.length, 2);
    assert.equal(key.value, '');
    assert.equal(key.focused, true);

    key.value = 'browser-login-password';
    act('model-api-key', 'beforeinput', {isTrusted: true, inputType: 'insertText'});
    assert.equal(key.value, '');
    key.value = 'typed-api-key';
    act('model-api-key', 'input', {isTrusted: true, inputType: 'insertText'});
    await act('toggle-model-key');
    assert.equal(key.props.type, 'text');
    assert.equal(key.value, 'typed-api-key');
    address.value = 'https://custom.example/v1';
    change();
    await act('test-model-btn');
    assert.equal(key.props.type, 'password');
    assert.deepEqual(calls.at(-1), {
        model_merchant: 'deepseek', model_name: 'new-model',
        base_url: 'https://custom.example/v1', key: 'typed-api-key', testOnly: true, personal: true
    });
    await act('toggle-model-key');
    await act('cancel-modify-btn');
    assert.equal(key.props.type, 'password');
    assert.equal(key.value, '');
});

test('clearing history requires confirmation and emits completion after the server succeeds', async () => {
    const confirmation = pending();
    const deletion = pending();
    const events = [];
    const {act, element, update, alerts} = mount({
        clearHistoryAPI: () => {
            events.push('delete');
            return deletion.promise;
        }
    }, {
        chatBusy: true,
        confirmAction: options => {
            assert.equal(options.destructive, true);
            assert.match(options.description, /所有聊天记录和聊天摘要/);
            events.push('confirm');
            return confirmation.promise;
        },
        onClearingHistory: busy => events.push(busy),
        onHistoryCleared: () => events.push('cleared')
    });

    await act('clear-chat-history');
    assert.match(element('chat-settings-status').text, /当前回复完成/);
    assert.deepEqual(events, []);
    update({chatBusy: false});
    const operation = act('clear-chat-history');
    assert.deepEqual(events, ['confirm']);
    confirmation.resolve('');
    await tick();
    assert.deepEqual(events, ['confirm', true, 'delete']);
    assert.equal(alerts.length, 0);
    deletion.resolve({ok: true});
    await operation;
    assert.deepEqual(events, ['confirm', true, 'delete', 'cleared', false]);
    assert.match(alerts.at(-1), /聊天记录已清空/);
});
