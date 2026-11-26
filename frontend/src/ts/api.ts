import type {ModelInfo, ChatResponse, InputLimits} from './global_config.ts';
import type {Preferences} from './preferences.ts';

const base_url = window.location.origin;
let requestGeneration = 0;
let sessionRequests = new AbortController();
let currentUser: AuthUser | undefined;

export class AuthRequiredError extends Error {}
export class CancelledRequestError extends Error {}
export class UserCancelledChatError extends Error {}
export class ChatNotStartedError extends Error {
    constructor(message: string, readonly retryable = true) { super(message); }
}
export function getRequestGeneration() { return requestGeneration; }
// 账号切换时取消旧请求，并用请求代数拦住迟到的响应。
export function invalidateRequests(user?: AuthUser) {
    requestGeneration++;
    sessionRequests.abort();
    sessionRequests = new AbortController();
    currentUser = user;
}

function toChatNotStarted(status: number, detailMessage?: unknown): ChatNotStartedError {
    const retryable = status !== 422;
    const fallback = status === 403 ? '请先在设置中配置个人模型和 API Key' :
        retryable ? '消息尚未发送，请重试' : '消息尚未发送，请修改后重新发送';
    return new ChatNotStartedError(typeof detailMessage === 'string' ? detailMessage : fallback, retryable);
}

async function requestJSON<T>(path: string, options: RequestInit, fallback: string,
                              mode: 'session' | 'public' | 'logout' | 'session-mutation' = 'session'): Promise<T> {
    const generation = requestGeneration;
    // 写会话 Cookie 的请求需要正常结束，界面失效时只取消普通请求。
    const sessionBound = mode === 'session';
    const user = mode !== 'public' ? currentUser : undefined;
    let response: Response;
    try {
        response = await fetch(`${base_url}${path}`, {
            ...options, credentials: 'same-origin',
            ...(sessionBound ? {signal: options.signal
                ? AbortSignal.any([options.signal, sessionRequests.signal]) : sessionRequests.signal} : {}),
            headers: {
                'Content-Type': 'application/json',
                // 账号头用于发现旧标签页，真正的登录身份由 Cookie 决定。
                ...(user ? {'X-Xiaowan-User': String(user.id)} : {}),
                ...options.headers
            }
        });
    } catch (error) {
        if (sessionBound && generation !== requestGeneration) throw new CancelledRequestError();
        throw error;
    }
    const body = await response.json().catch(() => null);
    if (sessionBound && generation !== requestGeneration) throw new CancelledRequestError();
    if (response.status === 401) {
        if ((sessionBound || mode === 'session-mutation') && generation === requestGeneration) {
            window.dispatchEvent(new Event('auth-required'));
        }
        throw new AuthRequiredError(typeof body?.detail === 'string' ? body.detail : '请先登录');
    }
    // 后端明确表示未开始处理时，才给原消息加上失败标记。
    if (path.startsWith('/chat') && (response.status === 403 || response.status === 422 || response.status === 409 || response.status === 429 || response.status === 503) &&
        body?.detail?.code === 'CHAT_NOT_STARTED') {
        throw toChatNotStarted(response.status, body.detail.message);
    }
    if (!response.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : fallback);
    if (body === null) throw new Error(fallback);
    return body as T;
}

export interface ChatStreamOptions {
    turnId?: string;
    signal?: AbortSignal;
    onStatus?: (stage: string, label: string) => void;
}

async function streamChatRequest(path: string, payload: Record<string, unknown>, fallback: string,
                                 options: ChatStreamOptions = {}): Promise<ChatResponse> {
    const generation = requestGeneration;
    const signals: AbortSignal[] = [AbortSignal.timeout(180000), sessionRequests.signal];
    if (options.signal) signals.push(options.signal);
    let response: Response;
    try {
        response = await fetch(`${base_url}${path}`, {
            method: 'POST',
            credentials: 'same-origin',
            signal: AbortSignal.any(signals),
            headers: {
                'Content-Type': 'application/json',
                ...(currentUser ? {'X-Xiaowan-User': String(currentUser.id)} : {}),
            },
            body: JSON.stringify(payload),
        });
    } catch (error) {
        if (generation !== requestGeneration) throw new CancelledRequestError();
        if (options.signal?.aborted) throw new UserCancelledChatError();
        throw error;
    }

    if (generation !== requestGeneration) throw new CancelledRequestError();
    if (!response.ok) {
        const body = await response.json().catch(() => null);
        if (generation !== requestGeneration) throw new CancelledRequestError();
        if (response.status === 401) {
            window.dispatchEvent(new Event('auth-required'));
            throw new AuthRequiredError(typeof body?.detail === 'string' ? body.detail : '请先登录');
        }
        if (body?.detail?.code === 'CHAT_NOT_STARTED') {
            throw toChatNotStarted(response.status, body.detail.message);
        }
        throw new Error(typeof body?.detail === 'string' ? body.detail : fallback);
    }

    if (!response.body) {
        if (options.signal?.aborted) throw new UserCancelledChatError();
        throw new Error(fallback);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    try {
        while (true) {
            const {value, done} = await reader.read();
            if (generation !== requestGeneration) throw new CancelledRequestError();
            if (options.signal?.aborted) throw new UserCancelledChatError();
            if (value) buffer += decoder.decode(value, {stream: !done});
            else if (done) buffer += decoder.decode();

            let boundary = buffer.indexOf('\n\n');
            while (boundary >= 0) {
                const rawBlock = buffer.slice(0, boundary);
                buffer = buffer.slice(boundary + 2);
                boundary = buffer.indexOf('\n\n');

                const dataLines = rawBlock
                    .split(/\r?\n/)
                    .filter(line => line.startsWith('data:'))
                    .map(line => line.slice(5).trimStart());
                if (!dataLines.length) continue;
                const event = JSON.parse(dataLines.join('\n'));
                if (event.type === 'status') {
                    if (typeof event.stage === 'string' && typeof event.label === 'string') {
                        options.onStatus?.(event.stage, event.label);
                    }
                } else if (event.type === 'done') {
                    return {
                        response: Array.isArray(event.response) ? event.response : [],
                        conversation_round: typeof event.conversation_round === 'number' ? event.conversation_round : null,
                        saved: Boolean(event.saved),
                        ...(typeof event.created_at === 'number' ? {created_at: event.created_at} : {}),
                    };
                } else if (event.type === 'cancelled') {
                    throw new UserCancelledChatError();
                } else if (event.type === 'error') {
                    if (event.code === 'CHAT_NOT_STARTED') {
                        throw toChatNotStarted(Number(event.status) || 503, event.message);
                    }
                    throw new Error(typeof event.message === 'string' ? event.message : fallback);
                }
            }
            if (done) break;
        }
    } catch (error) {
        if (generation !== requestGeneration) throw new CancelledRequestError();
        if (options.signal?.aborted || error instanceof UserCancelledChatError) throw new UserCancelledChatError();
        throw error;
    } finally {
        reader.releaseLock();
    }

    if (options.signal?.aborted) throw new UserCancelledChatError();
    throw new Error(fallback);
}

export function chatAPI(userInput: string): Promise<ChatResponse> {
    return requestJSON('/chat', {
        method: 'POST', signal: AbortSignal.timeout(180000), body: JSON.stringify({user_input: userInput})
    }, '聊天请求失败');
}

export function chatStreamAPI(userInput: string, options: ChatStreamOptions = {}): Promise<ChatResponse> {
    return streamChatRequest('/chat/stream', {
        user_input: userInput,
        ...(options.turnId ? {turn_id: options.turnId} : {}),
    }, '聊天请求失败', options);
}

export function regenerateChatAPI(conversationRound: number): Promise<ChatResponse> {
    return requestJSON('/chat/regenerate', {
        method: 'POST', signal: AbortSignal.timeout(180000),
        body: JSON.stringify({conversation_round: conversationRound})
    }, '重新生成失败，请稍后重试');
}

export function regenerateChatStreamAPI(conversationRound: number,
                                        options: ChatStreamOptions = {}): Promise<ChatResponse> {
    return streamChatRequest('/chat/regenerate/stream', {
        conversation_round: conversationRound,
        ...(options.turnId ? {turn_id: options.turnId} : {}),
    }, '重新生成失败，请稍后重试', options);
}

export function cancelChatAPI(turnId?: string) {
    return requestJSON<{ok: boolean; cancelled: boolean}>('/chat/cancel', {
        method: 'POST', signal: AbortSignal.timeout(10000),
        body: JSON.stringify(turnId ? {turn_id: turnId} : {}),
    }, '取消请求失败');
}

export function historyAPI(num: number, front = true) {
    return requestJSON<{history_messages: import('./global_config.ts').ConversationItem[]}>('/history', {
        method: 'POST', signal: AbortSignal.timeout(15000), body: JSON.stringify({num, front})
    }, '历史对话加载失败');
}

export function clearHistoryAPI() {
    return requestJSON<{ok: boolean}>('/history', {method: 'DELETE'}, '清空聊天记录失败，请稍后重试');
}

export function deleteHistoryRoundAPI(conversationRound: number) {
    return requestJSON<{ok: boolean}>(`/history/${conversationRound}`, {
        method: 'DELETE', signal: AbortSignal.timeout(15000)
    }, '删除该轮对话失败，请稍后重试');
}

export function deleteAccountAPI(current_password: string) {
    return requestJSON<{ok: boolean}>('/auth/account', {
        method: 'DELETE', body: JSON.stringify({current_password})
    }, '注销账户失败，请稍后重试', 'session-mutation');
}

export function personaAPI() {
    return requestJSON<import('./global_config.ts').PersonaInfo>('/persona', {
        method: 'GET', signal: AbortSignal.timeout(15000)
    }, '角色信息加载失败');
}

export function modelAPI(model: ModelInfo, testOnly = false, apiKey?: string, personal = false) {
    const options = {
        model_merchant: model.model_merchant,
        model_name: model.model_name,
        ...(model.base_url === undefined ? {} : {base_url: model.base_url}),
        ...(apiKey === undefined ? {} : {api_key: apiKey})
    };
    return requestJSON<{status: boolean; info: string}>(`/model${personal ? '/personal' : ''}${testOnly ? '/test' : ''}`, {
        method: 'POST', signal: AbortSignal.timeout(30000),
        body: JSON.stringify(options)
    }, '模型配置请求失败');
}

export interface ModelOptions {
    default_provider: string;
    providers: Record<string, {
        default_model: string;
        default_address: string;
        names: string[];
        saved_model: string | null;
        saved_address: string | null;
        has_saved_key: boolean;
    }>;
}
export interface SavedSettings {
    model: ModelInfo | null;
    model_ready: boolean;
    preferences: Preferences;
    model_options?: ModelOptions;
}
export function settingsAPI(preferences?: Preferences): Promise<SavedSettings> {
    return requestJSON('/settings', {
        method: preferences ? 'POST' : 'GET', signal: AbortSignal.timeout(15000),
        ...(preferences ? {body: JSON.stringify(preferences)} : {})
    }, preferences ? '设置保存失败，请检查后端和配置文件。' : '设置读取失败，请稍后重试。');
}

export interface AuthUser { id: number; username: string; role: 'admin' | 'user'; }
export function loginAPI(username: string, password: string) {
    return requestJSON<{user: AuthUser}>('/auth/login', {
        method: 'POST', signal: AbortSignal.timeout(15000), body: JSON.stringify({username, password})
    }, '登录失败，请检查后端连接', 'public');
}
export function registerAPI(username: string, password: string, confirm_password: string) {
    return requestJSON<{user: AuthUser}>('/auth/register', {
        method: 'POST', signal: AbortSignal.timeout(15000), body: JSON.stringify({username, password, confirm_password})
    }, '注册失败，请检查输入后重试', 'public');
}
export function sessionAPI() {
    return requestJSON<{user: AuthUser | null; limits?: InputLimits}>('/auth/session', {
        method: 'GET', signal: AbortSignal.timeout(15000)
    }, '无法检查登录状态，请检查后端连接', 'public');
}
export function logoutAPI() {
    return requestJSON<{ok: boolean}>('/auth/logout', {
        method: 'POST', signal: AbortSignal.timeout(15000)
    }, '退出失败，请检查后端连接后重试', 'logout');
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
    }, '密码修改失败，请重试', 'session-mutation');
}
