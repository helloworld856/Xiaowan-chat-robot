import test from 'node:test';
import assert from 'node:assert/strict';
import {mountComponent, nextTick} from './vue-harness.mjs';

const savedProfile = {
    username: 'member', nickname: 'Saved', avatar_id: 'fox', avatar_url: '/avatars/system/fox.png',
    avatars: [
        {id: 'cat', name: '猫', url: '/avatars/system/cat.png'},
        {id: 'fox', name: '狐狸', url: '/avatars/system/fox.png'}
    ],
    default_avatar_id: 'cat'
};

function profileHarness(t, {
    load = async () => savedProfile,
    save = async payload => ({...savedProfile, ...payload}),
    limits = {},
    isAdmin = false,
    confirm = async () => null
} = {}) {
    const alerts = [];
    const posts = [];
    const profiles = [];
    const passwords = [];
    const deletions = [];
    const events = [];
    const window = new EventTarget();
    const previousWindow = globalThis.window;
    globalThis.window = window;
    for (const type of ['password-change-start', 'password-change-end', 'password-changed', 'account-deleted']) {
        window.addEventListener(type, () => events.push(type));
    }
    const view = mountComponent(new URL('../src/components/ProfileDialog.vue', import.meta.url), {
        imports: {
            '../ts/api.ts': {
                AuthRequiredError: class extends Error {},
                CancelledRequestError: class extends Error {},
                getRequestGeneration: () => 0,
                profileAPI: payload => {
                    if (!payload) return load();
                    posts.push(payload);
                    return save(payload);
                },
                async deleteAccountAPI(password) { deletions.push(password); },
                async changePasswordAPI(payload) { passwords.push(payload); }
            },
            '../ts/global_config.ts': {
                inputLimits: {max_nickname_chars: 32, password_min_length: 8, password_max_length: 20, ...limits}
            }
        },
        props: {
            isAdmin,
            showAlert: message => alerts.push(message),
            confirmAction: confirm,
            onProfileChange: profile => profiles.push(profile)
        }
    });
    t.after(() => {
        view.unmount();
        if (previousWindow === undefined) delete globalThis.window;
        else globalThis.window = previousWindow;
    });
    return {...view, alerts, posts, profiles, passwords, deletions, events};
}

test('loading profile shares the saved data and allows selecting the default avatar', async t => {
    const app = profileHarness(t);
    await app.session.open();
    assert.equal(app.element('profile-display-name').text, 'Saved');
    assert.equal(app.element('profile-preview').props.src, savedProfile.avatar_url);
    assert.deepEqual(app.profiles, [savedProfile]);
    app.act('profile-default-avatar');
    await nextTick();
    assert.equal(app.element('profile-preview').props.src, '/avatars/system/cat.png');
    await app.act('profile-form', 'submit');
    assert.equal(app.posts[0].avatar_id, 'cat');
});

test('account deletion requires confirmation and is unavailable to the built-in administrator', async t => {
    let confirmation = null;
    const app = profileHarness(t, {
        isAdmin: true,
        confirm: async options => {
            assert.equal(options.password, true);
            assert.equal(options.destructive, true);
            return confirmation;
        }
    });
    await app.session.open();
    assert.equal(app.element('profile-delete-account').props.disabled, true);
    await app.act('profile-delete-account');
    assert.deepEqual(app.deletions, []);
    app.update({isAdmin: false});
    await app.act('profile-delete-account');
    assert.deepEqual(app.deletions, []);
    confirmation = 'current-test-password';
    await app.act('profile-delete-account');
    assert.deepEqual(app.deletions, [confirmation]);
    assert.deepEqual(app.events, ['account-deleted']);
});

test('a failed profile read cannot save an uninitialized draft', async t => {
    const app = profileHarness(t, {load: async () => { throw new Error('GET failed'); }});
    await app.session.open();
    assert.equal(app.element('profile-fields').props.disabled, true);
    assert.equal(app.element('profile-save').props.disabled, true);
    app.input('profile-nickname', 'New nickname');
    await app.act('profile-form', 'submit');
    assert.deepEqual(app.posts, []);
});

test('a configured nickname limit counts Unicode characters', async t => {
    const app = profileHarness(t, {limits: {max_nickname_chars: 3}});
    await app.session.open();
    app.input('profile-nickname', '🙂🙂🙂🙂');
    await app.act('profile-form', 'submit');
    assert.deepEqual(app.posts, []);
    assert.match(app.alerts.at(-1), /3/);
    app.input('profile-nickname', '🙂🙂🙂');
    await app.act('profile-form', 'submit');
    assert.equal(app.posts[0].nickname, '🙂🙂🙂');
});

test('profile save uses alerts, has no progress text and keeps the draft after failure', async t => {
    let rejectSave;
    const app = profileHarness(t, {save: async payload => {
        if (rejectSave) return {...savedProfile, ...payload};
        return new Promise((_, reject) => { rejectSave = reject; });
    }});
    await app.session.open();
    app.input('profile-nickname', 'New name');
    const pending = app.act('profile-form', 'submit');
    await nextTick();
    assert.equal(app.element('profile-status').text, '');
    assert.equal(app.element('profile-save').props.disabled, true);
    rejectSave(new Error('保存个人资料失败'));
    await pending;
    assert.deepEqual(app.alerts, ['保存个人资料失败']);
    assert.equal(app.element('profile-nickname').value, 'New name');
    assert.equal(app.element('profile-dialog').open, true);
    await app.act('profile-form', 'submit');
    assert.equal(app.alerts.at(-1), '个人资料已保存。');
    assert.equal(app.element('profile-dialog').open, false);
    assert.equal(app.profiles.at(-1).nickname, 'New name');
});

test('changing a password enforces character limits and signals the auth flow', async t => {
    const app = profileHarness(t);
    await app.session.open();
    await app.act('profile-open-password');
    app.element('profile-current-password').value = 'Old!12';
    const submit = async value => {
        app.element('profile-new-password').value = value;
        app.element('profile-confirm-password').value = value;
        await app.act('profile-password-form', 'submit');
    };
    await submit('Short!1');
    await submit('🙂'.repeat(21));
    assert.equal(app.passwords.length, 0);
    assert.match(app.element('profile-password-status').text, /8–20/);
    const valid = '🙂'.repeat(20);
    await submit(valid);
    assert.equal(app.passwords[0].new_password, valid);
    assert.equal(app.passwords[0].current_password, 'Old!12');
    assert.equal(app.element('profile-current-password').value, '');
    assert.deepEqual(app.events, ['password-change-start', 'password-changed', 'password-change-end']);
});
