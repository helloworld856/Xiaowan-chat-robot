export interface Preferences {
    reply_length: 'short' | 'balanced' | 'detailed';
    split_reply: boolean;
    playback_mode: 'sequential' | 'instant';
    reply_interval_ms: number;
    send_shortcut: 'enter' | 'ctrl_enter';
}

export const preferences: Preferences = {
    reply_length: 'balanced', split_reply: true, playback_mode: 'sequential',
    reply_interval_ms: 1000, send_shortcut: 'enter'
};

export function replyDelay(settings: Pick<Preferences, 'playback_mode' | 'reply_interval_ms'>) {
    return settings.playback_mode === 'instant' ? 0 : settings.reply_interval_ms;
}

export function shouldSendMessage(event: Pick<KeyboardEvent, 'key' | 'shiftKey' | 'ctrlKey' | 'metaKey' | 'altKey' | 'isComposing' | 'keyCode'>, shortcut: Preferences['send_shortcut']) {
    if (event.key !== 'Enter' || event.isComposing || event.keyCode === 229 || event.shiftKey || event.altKey) return false;
    return shortcut === 'ctrl_enter' ? event.ctrlKey || event.metaKey : !event.ctrlKey && !event.metaKey;
}
