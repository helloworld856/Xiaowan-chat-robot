import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {compileModule, mountComponent, nextTick} from './vue-harness.mjs';

const dialogs = compileModule(readFileSync(new URL('../src/ts/dialogs.ts', import.meta.url), 'utf8'));

test('an obsolete close animation cannot complete a newer dialog close', async () => {
    const classes = new Set();
    let cancelFirst;
    let finishSecond;
    const firstAnimation = new Promise((_, reject) => { cancelFirst = reject; });
    const secondAnimation = new Promise(resolve => { finishSecond = resolve; });
    let animation = firstAnimation;
    const dialog = {
        open: true, children: [],
        classList: {add: name => classes.add(name), remove: (...names) => names.forEach(name => classes.delete(name)), contains: name => classes.has(name)},
        getAnimations: () => [{finished: animation}],
        close() { this.open = false; }
    };
    const oldClose = dialogs.closeDialog(dialog);
    dialogs.openDialog(dialog);
    animation = secondAnimation;
    const newClose = dialogs.closeDialog(dialog);
    cancelFirst(new Error('first animation canceled'));
    await oldClose;
    assert.equal(dialog.open, true);
    finishSecond();
    await newClose;
    assert.equal(dialog.open, false);
});

test('a destructive confirmation starts with an empty password', async t => {
    const view = mountComponent(new URL('../src/components/AppDialogs.vue', import.meta.url), {
        imports: {'../ts/dialogs.ts': dialogs}
    });
    t.after(view.unmount);
    const input = view.element('data-confirm-password');
    input.value = 'old browser-filled password';
    const confirmation = view.session.confirmAction({title: '注销账户？', description: '请输入密码', action: '注销', password: true});
    await nextTick();
    assert.equal(input.value, '');
    view.act('data-confirm-form', 'submit');
    await nextTick();
    assert.equal(view.element('data-confirm-dialog').open, true);
    assert.equal(view.element('alert-window-message').text, '请输入当前密码');
    await view.act('alert-window-container', 'cancel');
    input.value = 'current password';
    view.act('data-confirm-form', 'submit');
    assert.equal(await confirmation, 'current password');
    assert.equal(input.value, '');
});

test('shared alerts preserve every result until dismissed and clear the queue on signout', async t => {
    let closed = 0;
    const view = mountComponent(new URL('../src/components/AppDialogs.vue', import.meta.url), {
        imports: {'../ts/dialogs.ts': dialogs},
        props: {onAlertClosed: () => closed++}
    });
    t.after(view.unmount);
    const dialog = view.element('alert-window-container');
    const message = view.element('alert-window-message');

    view.session.showAlert('Preference save succeeded');
    view.session.showAlert('Chat send failed');
    await nextTick();
    assert.equal(dialog.open, true);
    assert.equal(message.text, 'Preference save succeeded');

    const dismiss = view.act('alert-window-container', 'cancel');
    view.session.showAlert('History cleared');
    await dismiss;
    await nextTick();
    assert.equal(dialog.open, true);
    assert.equal(message.text, 'Chat send failed');
    assert.equal(closed, 0);

    await view.act('alert-window-container', 'cancel');
    await nextTick();
    assert.equal(dialog.open, true);
    assert.equal(message.text, 'History cleared');
    await view.act('alert-window-container', 'cancel');
    assert.equal(dialog.open, false);
    assert.equal(closed, 1);

    view.session.showAlert('Old account');
    view.session.showAlert('Old account queued');
    view.session.reset();
    view.session.showAlert('New account');
    await nextTick();
    assert.equal(message.text, 'New account');
    await view.act('alert-window-container', 'cancel');
    assert.equal(dialog.open, false);
});
