export interface Preferences {
    send_shortcut: 'enter' | 'ctrl_enter';
}

export const preferences: Preferences = {
    send_shortcut: 'enter'
};

export function shouldSendMessage(event: Pick<KeyboardEvent, 'key' | 'shiftKey' | 'ctrlKey' | 'metaKey' | 'altKey' | 'isComposing' | 'keyCode'>, shortcut: Preferences['send_shortcut']) {
    if (event.key !== 'Enter' || event.isComposing || event.keyCode === 229 || event.shiftKey || event.altKey) return false;
    return shortcut === 'ctrl_enter' ? event.ctrlKey || event.metaKey : !event.ctrlKey && !event.metaKey;
}
