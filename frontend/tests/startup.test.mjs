import test from 'node:test';
import assert from 'node:assert/strict';
import {defineComponent, h, ref} from 'vue';
import {mountComponent, nextTick} from './vue-harness.mjs';

const account = {id: 1, username: 'member', role: 'user'};
const profile = {nickname: 'Saved nickname', avatar_url: '/avatars/system/fox.png'};
const persona = {BOT_NAME: '小晚', BOT_AVATAR: '/avatars/xiaowan.png', USER_AVATAR: '/avatars/system/cat.png'};
const preferences = {theme: 'theme3', send_shortcut: 'ctrl_enter', chat_font_size: 'large'};

function startupHarness(t, loadPersona = async () => persona, savedPreferences = preferences) {
    const document = {documentElement: {className: 'theme0'}, title: ''};
    const originalDocument = globalThis.document;
    const originalStorage = globalThis.localStorage;
    globalThis.document = document;
    const calls = [];
    let storageAccesses = 0;
    globalThis.localStorage = {
        getItem() { storageAccesses++; },
        setItem() { storageAccesses++; }
    };
    let generation = 0;
    let handlers;
    const user = ref(null);
    const starting = ref(false);
    const component = (id, methods, render) => defineComponent({
        props: ['ready', 'profile', 'persona', 'displayName', 'shortcut'],
        emits: ['profile-change', 'preferences-change'],
        setup(props, {emit, expose}) {
            expose(methods(emit));
            return () => h('div', {id}, render?.(props));
        }
    });
    const empty = () => ({});
    const reset = () => ({reset() {}});
    const app = mountComponent(new URL('../src/App.vue', import.meta.url), {
        imports: {
            './components/AccountMenu.vue': {__esModule: true, default: component('test-menu', reset, props => props.displayName)},
            './components/AppDialogs.vue': {__esModule: true, default: component('test-alerts', reset)},
            './components/AuthScreen.vue': {__esModule: true, default: component('test-login', empty)},
            './components/ChatWorkspace.vue': {__esModule: true, default: component('test-chat', () => ({
                async loadHistory() { calls.push('history'); },
                reset() { calls.push('reset-chat'); }
            }), props => [String(props.ready), props.profile?.avatar_url, props.persona?.BOT_AVATAR, props.shortcut].join('|'))},
            './components/ProfileDialog.vue': {__esModule: true, default: component('test-profile', emit => ({
                async load() { calls.push('profile'); emit('profile-change', profile); },
                reset() {}
            }))},
            './components/SettingsDialog.vue': {__esModule: true, default: component('test-settings', emit => ({
                async load() { calls.push('settings'); emit('preferences-change', savedPreferences); },
                reset() {}
            }))},
            './composables/useSession.ts': {useSession(options) {
                handlers = options;
                return {user, starting, sessionHidden: ref(false), loggingOut: ref(false), busy: ref(false)};
            }},
            './ts/api.ts': {
                AuthRequiredError: class extends Error {},
                CancelledRequestError: class extends Error {},
                getRequestGeneration: () => generation,
                async personaAPI() { calls.push('persona'); return loadPersona(); }
            }
        }
    });
    t.after(() => {
        app.unmount();
        if (originalDocument === undefined) delete globalThis.document;
        else globalThis.document = originalDocument;
        if (originalStorage === undefined) delete globalThis.localStorage;
        else globalThis.localStorage = originalStorage;
    });
    return {
        ...app, document, calls, storageAccesses: () => storageAccesses,
        login() { user.value = account; generation++; return handlers.onAuthenticated(account); },
        logout() { user.value = null; generation++; handlers.onSignedOut(); }
    };
}

test('startup loads profile before persona and applies server preferences without browser storage', async t => {
    const app = startupHarness(t);
    assert.equal(app.element('app-shell').props.hidden, true);
    await app.login();
    await nextTick();
    assert.deepEqual(app.calls, ['profile', 'persona', 'settings', 'history']);
    assert.equal(app.element('test-menu').text, 'Saved nickname');
    assert.equal(app.element('test-chat').text, 'true|/avatars/system/fox.png|/avatars/xiaowan.png|ctrl_enter');
    assert.equal(app.document.title, '小晚');
    assert.equal(app.document.documentElement.className, 'theme3');
    assert.equal(app.element('app-shell').props['data-chat-font-size'], 'large');
    assert.equal(app.storageAccesses(), 0);
});

test('signout clears account data and ignores a late persona response', async t => {
    let resolvePersona;
    const app = startupHarness(t, () => new Promise(resolve => { resolvePersona = resolve; }));
    const login = app.login();
    await nextTick();
    assert.equal(app.element('test-menu').text, 'Saved nickname');
    app.logout();
    resolvePersona(persona);
    await login;
    await nextTick();
    assert.deepEqual(app.calls, ['profile', 'persona', 'reset-chat']);
    assert.equal(app.element('app-shell').props.hidden, true);
    assert.equal(app.element('test-menu').text, '');
    assert.equal(app.element('test-chat').text, 'false|||enter');
    assert.equal(app.document.documentElement.className, 'theme0');
    assert.equal(app.element('app-shell').props['data-chat-font-size'], 'standard');
});

test('the dark theme applies on login and resets to default light on signout', async t => {
    const app = startupHarness(t, async () => persona, {...preferences, theme: 'theme4'});
    await app.login();
    await nextTick();
    assert.equal(app.document.documentElement.className, 'theme4');
    app.logout();
    assert.equal(app.document.documentElement.className, 'theme0');
});
