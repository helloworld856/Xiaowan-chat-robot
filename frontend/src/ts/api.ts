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

export interface CancellableChatOptions {
    turnId?: string;
    signal?: AbortSignal;
    onBubble?: (text: string) => void;
}

async function requestJSON<T>(path: string, options: RequestInit, fallback: string,
                              mode: 'session' | 'public' | 'logout' | 'session-mutation' = 'session',
                              userSignal?: AbortSignal): Promise<T> {
    const generation = requestGeneration;
    // 写会话 Cookie 的请求需要正常结束，界面失效时只取消普通请求。
    const sessionBound = mode === 'session';
    const user = mode !== 'public' ? currentUser : undefined;
    const signals: AbortSignal[] = [];
    if (options.signal) signals.push(options.signal);
    if (userSignal) signals.push(userSignal);
    if (sessionBound) signals.push(sessionRequests.signal);
    let response: Response;
    try {
        response = await fetch(`${base_url}${path}`, {
            ...options, credentials: 'same-origin',
            ...(signals.length ? {signal: signals.length === 1 ? signals[0] : AbortSignal.any(signals)} : {}),
            headers: {
                'Content-Type': 'application/json',
                // 账号头用于发现旧标签页，真正的登录身份由 Cookie 决定。
                ...(user ? {'X-Xiaowan-User': String(user.id)} : {}),
                ...options.headers
            }
        });
    } catch (error) {
        if (sessionBound && generation !== requestGeneration) throw new CancelledRequestError();
        if (userSignal?.aborted) throw new UserCancelledChatError();
        throw error;
    }
    const body = await response.json().catch(() => null);
    if (sessionBound && generation !== requestGeneration) throw new CancelledRequestError();
    if (userSignal?.aborted || response.status === 499 || body?.detail?.code === 'CHAT_CANCELLED') {
        throw new UserCancelledChatError();
    }
    if (response.status === 401) {
        if ((sessionBound || mode === 'session-mutation') && generation === requestGeneration) {
            window.dispatchEvent(new Event('auth-required'));
        }
        throw new AuthRequiredError(typeof body?.detail === 'string' ? body.detail : '请先登录');
    }
    // 后端明确表示未开始处理时，才给原消息加上失败标记。
    if (path.startsWith('/chat') && (response.status === 403 || response.status === 422 || response.status === 409 || response.status === 429 || response.status === 503) &&
        body?.detail?.code === 'CHAT_NOT_STARTED') {
        const retryable = response.status !== 422;
        const message = response.status === 403 ? '请先在设置中配置个人模型和 API Key' :
            retryable ? '消息尚未发送，请重试' : '消息尚未发送，请修改后重新发送';
        throw new ChatNotStartedError(typeof body.detail.message === 'string' ? body.detail.message : message, retryable);
    }
    if (!response.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : fallback);
    if (body === null) throw new Error(fallback);
    return body as T;
}

function handleChatErrorResponse(path: string, response: Response, body: any, fallback: string,
                                 sessionBound: boolean, generation: number, userSignal?: AbortSignal): never {
    if (sessionBound && generation !== requestGeneration) throw new CancelledRequestError();
    if (userSignal?.aborted || response.status === 499 || body?.detail?.code === 'CHAT_CANCELLED') {
        throw new UserCancelledChatError();
    }
    if (response.status === 401) {
        if (sessionBound && generation === requestGeneration) {
            window.dispatchEvent(new Event('auth-required'));
        }
        throw new AuthRequiredError(typeof body?.detail === 'string' ? body.detail : '请先登录');
    }
    if (path.startsWith('/chat') && (response.status === 403 || response.status === 422 || response.status === 409 || response.status === 429 || response.status === 503) &&
        body?.detail?.code === 'CHAT_NOT_STARTED') {
        const retryable = response.status !== 422;
        const message = response.status === 403 ? '请先在设置中配置个人模型和 API Key' :
            retryable ? '消息尚未发送，请重试' : '消息尚未发送，请修改后重新发送';
        throw new ChatNotStartedError(typeof body.detail.message === 'string' ? body.detail.message : message, retryable);
    }
    throw new Error(typeof body?.detail === 'string' ? body.detail : fallback);
}

async function requestChatStream(payload: {user_input: string; turn_id?: string},
                                 options: CancellableChatOptions): Promise<ChatResponse> {
    const path = '/chat/stream';
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
                Accept: 'text/event-stream',
                ...(currentUser ? {'X-Xiaowan-User': String(currentUser.id)} : {}),
            },
            body: JSON.stringify(payload),
        });
    } catch (error) {
        if (generation !== requestGeneration) throw new CancelledRequestError();
        if (options.signal?.aborted) throw new UserCancelledChatError();
        throw error;
    }

    if (!response.ok) {
        const body = await response.json().catch(() => null);
        handleChatErrorResponse(path, response, body, '聊天请求失败', true, generation, options.signal);
    }

    const contentType = response.headers.get('content-type') || '';
    if (!contentType.includes('text/event-stream') || !response.body) {
        const body = await response.json().catch(() => null);
        if (generation !== requestGeneration) throw new CancelledRequestError();
        if (options.signal?.aborted) throw new UserCancelledChatError();
        if (body === null) throw new Error('聊天请求失败');
        return body as ChatResponse;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finalResponse: ChatResponse | null = null;

    const processBlock = (block: string) => {
        let eventName = 'message';
        const dataLines: string[] = [];
        for (const line of block.split('\n')) {
            if (line.startsWith('event:')) eventName = line.slice(6).trim();
            else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
        }
        if (!dataLines.length) return;
        const parsed = JSON.parse(dataLines.join('\n'));
        if (eventName === 'bubble') {
            if (typeof parsed?.text === 'string' && parsed.text) options.onBubble?.(parsed.text);
        } else if (eventName === 'done') {
            finalResponse = parsed as ChatResponse;
        } else if (eventName === 'cancelled') {
            throw new UserCancelledChatError();
        } else if (eventName === 'error') {
            throw new Error(typeof parsed?.message === 'string' ? parsed.message : '聊天请求失败');
        }
    };

    try {
        while (true) {
            const {value, done} = await reader.read();
            if (generation !== requestGeneration) throw new CancelledRequestError();
            if (options.signal?.aborted) throw new UserCancelledChatError();
            if (value) buffer += decoder.decode(value, {stream: true}).replace(/\r\n/g, '\n');
            if (done) buffer += decoder.decode().replace(/\r\n/g, '\n');

            let sep = buffer.indexOf('\n\n');
            while (sep !== -1) {
                const block = buffer.slice(0, sep).trim();
                buffer = buffer.slice(sep + 2);
                if (block) processBlock(block);
                sep = buffer.indexOf('\n\n');
            }
            if (done) {
                const trailing = buffer.trim();
                if (trailing) processBlock(trailing);
                break;
            }
        }
    } catch (error) {
        if (generation !== requestGeneration) throw new CancelledRequestError();
        if (options.signal?.aborted) throw new UserCancelledChatError();
        throw error;
    } finally {
        reader.releaseLock();
    }

    if (generation !== requestGeneration) throw new CancelledRequestError();
    if (options.signal?.aborted) throw new UserCancelledChatError();
    if (finalResponse === null) throw new Error('聊天请求失败');
    return finalResponse;
}

export function chatAPI(userInput: string, options?: CancellableChatOptions): Promise<ChatResponse> {
    const payload = options?.turnId
        ? {user_input: userInput, turn_id: options.turnId}
        : {user_input: userInput};
    if (options?.onBubble) {
        return requestChatStream(payload, options);
    }
    return requestJSON('/chat', {
        method: 'POST', signal: AbortSignal.timeout(180000), body: JSON.stringify(payload)
    }, '聊天请求失败', 'session', options?.signal);
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

export interface ManualPortrait {
    call_me: string;
    city: string;
    birthday: string;
    occupation: string;
    chat_preference: string;
}

export interface UserPortrait {
    manual: ManualPortrait;
    auto_learn: boolean;
    has_learned?: boolean;
}

export function portraitAPI(portrait?: {manual: ManualPortrait; auto_learn: boolean}): Promise<UserPortrait> {
    return requestJSON<UserPortrait>('/portrait', {
        method: portrait ? 'POST' : 'GET', signal: AbortSignal.timeout(15000),
        ...(portrait ? {body: JSON.stringify(portrait)} : {})
    }, portrait ? '保存用户画像失败，请稍后重试' : '读取用户画像失败，请稍后重试');
}

export function clearLearnedPortraitAPI() {
    return requestJSON<{ok: boolean; has_learned: boolean}>('/portrait/learned', {
        method: 'DELETE', signal: AbortSignal.timeout(15000),
    }, '清空印象失败，请稍后重试');
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
