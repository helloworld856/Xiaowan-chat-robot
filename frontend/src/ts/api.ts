import type {ModelInfo, ChatResponse} from './global_config.ts';
import type {Preferences} from './preferences.ts';

const base_url = window.location.origin;
let requestGeneration = 0;
let sessionRequests = new AbortController();

export class AuthRequiredError extends Error {}
export class CancelledRequestError extends Error {}
export function getRequestGeneration() { return requestGeneration; }
export function invalidateRequests() {
    requestGeneration++;
    sessionRequests.abort();
    sessionRequests = new AbortController();
}

async function requestJSON<T>(path: string, options: RequestInit, fallback: string, authEndpoint = false): Promise<T> {
    const generation = requestGeneration;
    let response: Response;
    try {
        response = await fetch(`${base_url}${path}`, {
            ...options, credentials: 'same-origin',
            ...(!authEndpoint ? {signal: options.signal
                ? AbortSignal.any([options.signal, sessionRequests.signal]) : sessionRequests.signal} : {}),
            headers: {'Content-Type': 'application/json', ...options.headers}
        });
    } catch (error) {
        if (!authEndpoint && generation !== requestGeneration) throw new CancelledRequestError();
        throw error;
    }
    const body = await response.json().catch(() => null);
    if (!authEndpoint && generation !== requestGeneration) throw new CancelledRequestError();
    if (response.status === 401) {
        if (!authEndpoint) window.dispatchEvent(new Event('auth-required'));
        throw new AuthRequiredError(typeof body?.detail === 'string' ? body.detail : '请先登录');
    }
    if (!response.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : fallback);
    if (body === null) throw new Error(fallback);
    return body as T;
}

export function chatAPI(userInput: string): Promise<ChatResponse> {
    return requestJSON('/chat', {method: 'POST', body: JSON.stringify({user_input: userInput})}, '聊天请求失败');
}

export function historyAPI(num: number, front = true) {
    return requestJSON<{history_messages: import('./global_config.ts').ConversationItem[]}>('/history', {
        method: 'POST', signal: AbortSignal.timeout(15000), body: JSON.stringify({num, front})
    }, '历史对话加载失败');
}

export function personaAPI() {
    return requestJSON<import('./global_config.ts').PersonaInfo>('/persona', {
        method: 'GET', signal: AbortSignal.timeout(15000)
    }, '角色信息加载失败');
}

export function modelAPI(model: ModelInfo, testOnly = false) {
    return requestJSON<{status: boolean; info: string}>(`/model${testOnly ? '/test' : ''}`, {
        method: 'POST', signal: AbortSignal.timeout(30000),
        body: JSON.stringify({model_merchant: model.model_merchant || '', model_name: model.model_name || '', base_url: model.base_url || ''})
    }, '模型配置请求失败');
}

export interface SavedSettings { model: ModelInfo | null; preferences: Preferences; }
export function settingsAPI(preferences?: Preferences): Promise<SavedSettings> {
    return requestJSON('/settings', {
        method: preferences ? 'POST' : 'GET', signal: AbortSignal.timeout(15000),
        ...(preferences ? {body: JSON.stringify(preferences)} : {})
    }, preferences ? '设置保存失败，请检查后端和配置文件。' : '设置读取失败，可在设置页重新读取。');
}

export interface AuthUser { username: string; }
export function loginAPI(username: string, password: string) {
    return requestJSON<{user: AuthUser}>('/auth/login', {
        method: 'POST', signal: AbortSignal.timeout(15000), body: JSON.stringify({username, password})
    }, '登录失败，请检查后端连接', true);
}
export function sessionAPI() {
    return requestJSON<{user: AuthUser | null}>('/auth/session', {
        method: 'GET', signal: AbortSignal.timeout(15000)
    }, '无法检查登录状态，请检查后端连接', true);
}
export function logoutAPI() {
    return requestJSON<{ok: boolean}>('/auth/logout', {
        method: 'POST', signal: AbortSignal.timeout(15000)
    }, '退出失败，请检查后端连接后重试', true);
}

export type AvatarId = 'cat' | 'fox' | 'rabbit' | 'bear' | 'penguin' | 'frog';
export interface ProfileInfo {
    username: string;
    nickname: string;
    avatar_id: AvatarId;
    avatar_url: string;
    avatars: {id: AvatarId; name: string; url: string}[];
    default_avatar_id: AvatarId;
}
export function profileAPI(profile?: {nickname: string; avatar_id: AvatarId}) {
    return requestJSON<ProfileInfo>('/profile', {
        method: profile ? 'POST' : 'GET', signal: AbortSignal.timeout(15000),
        ...(profile ? {body: JSON.stringify(profile)} : {})
    }, profile ? '个人资料保存失败，请重试' : '个人资料读取失败，请重试');
}
export function changePasswordAPI(passwords: {current_password: string; new_password: string; confirm_password: string}) {
    return requestJSON<{ok: boolean}>('/auth/password', {
        method: 'POST', signal: AbortSignal.timeout(15000), body: JSON.stringify(passwords)
    }, '密码修改失败，请重试');
}
