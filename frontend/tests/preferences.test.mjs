import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
import {runInNewContext} from 'node:vm';

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

test('message rendering recalculates delay for each reply and never waits after the last', async () => {
    const source = fs.readFileSync(new URL('../src/ts/utils.ts', import.meta.url), 'utf8');
    const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
    const exports = {};
    const delays = [];
    const rendered = [];
    const element = () => ({append() {}, appendChild() {}});
    runInNewContext(compiled, {
        exports,
        require: name => name === './ui.ts'
            ? {chatBox: {scrollTop: 0, clientHeight: 0, scrollHeight: 0, appendChild: row => rendered.push(row)}, scrollToBottom() {}}
            : (() => { throw new Error('Unexpected dependency: ' + name); })(),
        window: {USER_AVATAR: '', BOT_AVATAR: ''}, document: {createElement: element},
        console: {log() {}}, setTimeout: (resolve, milliseconds) => { delays.push(milliseconds); resolve(); }
    });
    await exports.addAssistantMessage(['hello', 'a'.repeat(20), 'b'.repeat(100), 'last']);
    assert.deepEqual(delays, [1500, 3000, 5000]);
    assert.equal(rendered.length, 4);
    delays.length = 0;
    await exports.addAssistantMessage(['only one reply']);
    assert.deepEqual(delays, []);
    assert.equal(rendered.length, 5);
});
