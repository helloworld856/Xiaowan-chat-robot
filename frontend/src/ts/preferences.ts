export interface Preferences {
    send_shortcut: 'enter' | 'ctrl_enter';
    theme: 'theme0' | 'theme1' | 'theme2' | 'theme3';
}

export function shouldSendMessage(event: Pick<KeyboardEvent, 'key' | 'shiftKey' | 'ctrlKey' | 'metaKey' | 'altKey' | 'isComposing' | 'keyCode'>, shortcut: Preferences['send_shortcut']) {
    // 输入法确认与 Shift+Enter 保留给输入框，避免误发。
    if (event.key !== 'Enter' || event.isComposing || event.keyCode === 229 || event.shiftKey || event.altKey) return false;
    return shortcut === 'ctrl_enter' ? event.ctrlKey || event.metaKey : !event.ctrlKey && !event.metaKey;
}
