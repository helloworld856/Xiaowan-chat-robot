import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';

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

test('instant playback skips delay; sequential playback uses selected milliseconds', () => {
    assert.equal(typeof prefs.replyDelay, 'function');
    assert.equal(prefs.replyDelay({playback_mode: 'instant', reply_interval_ms: 2000}), 0);
    assert.equal(prefs.replyDelay({playback_mode: 'sequential', reply_interval_ms: 1500}), 1500);
    assert.equal(prefs.replyDelay({playback_mode: 'sequential', reply_interval_ms: 0}), 0);
});
