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
    input.value = 'current password';
    view.act('data-confirm-form', 'submit');
    assert.equal(await confirmation, 'current password');
    assert.equal(input.value, '');
});
