import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
import {mountComponent, nextTick} from './vue-harness.mjs';

const sourcePath = new URL('../src/ts/preferences.ts', import.meta.url);
let prefs;
try {
    const source = fs.readFileSync(sourcePath, 'utf8');
    const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ESNext}}).outputText;
    prefs = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'));
} catch (error) {
    prefs = {loadError: error};
}

test('Enter sends, Shift+Enter and IME confirmation preserve input', () => {
    assert.equal(typeof prefs.shouldSendMessage, 'function', 'keyboard preferences not implemented');
    const enter = {key: 'Enter', shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, isComposing: false, keyCode: 13};
    assert.equal(prefs.shouldSendMessage(enter, 'enter'), true);
    assert.equal(prefs.shouldSendMessage({...enter, shiftKey: true}, 'enter'), false);
    assert.equal(prefs.shouldSendMessage({...enter, isComposing: true}, 'enter'), false);
    assert.equal(prefs.shouldSendMessage({...enter, keyCode: 229}, 'enter'), false);
    assert.equal(prefs.shouldSendMessage({...enter, key: 'a'}, 'enter'), false);
});

test('Ctrl+Enter mode leaves ordinary Enter for newlines', () => {
    assert.equal(typeof prefs.shouldSendMessage, 'function');
    const enter = {key: 'Enter', shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, isComposing: false, keyCode: 13};
    assert.equal(prefs.shouldSendMessage(enter, 'ctrl_enter'), false);
    assert.equal(prefs.shouldSendMessage({...enter, ctrlKey: true}, 'ctrl_enter'), true);
    assert.equal(prefs.shouldSendMessage({...enter, metaKey: true}, 'ctrl_enter'), true);
    assert.equal(prefs.shouldSendMessage({...enter, ctrlKey: true, shiftKey: true}, 'ctrl_enter'), false);
});

test('the composer keeps unfinished IME text in the input until it is committed', async () => {
    const originalResizeObserver = globalThis.ResizeObserver;
    globalThis.ResizeObserver = class { observe() {} disconnect() {} };
    let app;
    try {
        let draft = '你好';
        const sent = [];
        app = mountComponent(new URL('../src/components/ChatComposer.vue', import.meta.url), {
            imports: {'../ts/preferences.ts': prefs},
            props: {
                locked: false, shortcut: 'enter', modelValue: draft,
                'onUpdate:modelValue': value => {
                    draft = value;
                    app.update({modelValue: value});
                },
                onSend: () => sent.push(draft)
            }
        });
        const input = app.element('userInput');
        input.dispatchEvent = event => app.act('userInput', event.type);
        input.clientHeight = 24;
        input.scrollHeight = 48;
        const toggle = app.element('chat-input-toggle');
        const sendButton = app.element('inputArea').children.find(child => child.tag === 'button');
        const pressEnter = () => app.act('userInput', 'keydown', {
            key: 'Enter', keyCode: 13, isComposing: false
        });
        app.act('userInput', 'compositionstart');
        app.input('userInput', '你好，小晚');
        await nextTick();
        assert.equal(toggle.props.hidden, false, 'unfinished text can overflow the collapsed input');
        app.act('chat-input-toggle');
        await nextTick();
        let escapeBubbled = true;
        app.act('chat-composer', 'keydown', {
            key: 'Escape', keyCode: 27, isComposing: false,
            stopPropagation() { escapeBubbled = false; }
        });
        assert.equal(escapeBubbled, false, 'Escape handled by the IME should stay inside the composer');
        assert.equal(toggle.props['aria-expanded'], true);
        pressEnter();
        sendButton.props.onClick();
        assert.deepEqual(sent, []);
        assert.equal(input.value, '你好，小晚');

        app.act('userInput', 'compositionend');
        await nextTick();
        pressEnter();
        assert.deepEqual(sent, ['你好，小晚']);
    } finally {
        app?.unmount();
        if (originalResizeObserver === undefined) delete globalThis.ResizeObserver;
        else globalThis.ResizeObserver = originalResizeObserver;
    }
});

test('the system theme resolves to light or dark and other themes pass through', () => {
    assert.equal(prefs.resolveTheme('system', true), 'theme4');
    assert.equal(prefs.resolveTheme('system', false), 'theme0');
    assert.equal(prefs.resolveTheme('theme2', true), 'theme2');
    assert.equal(prefs.resolveTheme('theme4', false), 'theme4');
});
