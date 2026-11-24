import {computed, onBeforeUnmount, onMounted, ref, shallowRef} from 'vue';
import {AuthRequiredError, invalidateRequests, loginAPI, logoutAPI, registerAPI, sessionAPI} from '../ts/api.ts';
import type {AuthUser} from '../ts/api.ts';
import type {ConfirmOptions} from '../ts/dialogs.ts';
import {inputLimits} from '../ts/global_config.ts';

const authChangeKey = 'xiaowan.auth-changed';
const authChangeMessage = '此浏览器的登录状态已更改，请重新登录';
const expiredMessage = '登录已过期，请重新登录';

interface SessionOptions {
    onAuthenticated: (user: AuthUser) => Promise<void>;
    onSignedOut: () => void;
    showAlert: (message: string) => void;
    confirmAction: (options: ConfirmOptions) => Promise<string | null>;
}

function notifyOtherTabs() {
    try {
        // 标签页只交换状态变化信号，会话身份仍由服务端根据 Cookie 校验。
        localStorage.setItem(authChangeKey, `${Date.now()}:${Math.random()}`);
    } catch {
        // 存储不可用时，私有请求仍由服务端校验当前账号。
    }
}

export function useSession({onAuthenticated, onSignedOut, showAlert, confirmAction}: SessionOptions) {
    const user = shallowRef<AuthUser | null>(null);
    const starting = ref(true);
    const sessionHidden = ref(false);
    const username = ref('');
    const password = ref('');
    const registerUsername = ref('');
    const registerPassword = ref('');
    const registerConfirmation = ref('');
    const registrationOpen = ref(false);
    const loginMessage = ref('');
    const loginTone = ref<'error' | 'success'>('error');
    const registrationMessage = ref('');
    const pending = ref<'login' | 'register' | 'logout' | null>(null);
    const changingPassword = ref(false);
    const busy = computed(() => pending.value !== null || changingPassword.value);
    const loggingIn = computed(() => pending.value === 'login');
    const registering = computed(() => pending.value === 'register');
    const loggingOut = computed(() => pending.value === 'logout');

    let disposed = false;
    // 跨标签变更也会使尚未完成的登录或启动探测失效。
    let authRevision = 0;
    let externalAuthChange = 0;
    let restoreCheck = 0;
    let logoutConfirmation: number | undefined;

    function showRegistration(show: boolean) {
        registrationOpen.value = show;
        password.value = '';
        registerPassword.value = '';
        registerConfirmation.value = '';
        registrationMessage.value = '';
    }

    function switchForm(show: boolean) {
        if (busy.value) return false;
        if (show) loginMessage.value = '';
        showRegistration(show);
        return true;
    }

    function signedOut(message = '', tone: 'error' | 'success' = 'error') {
        authRevision++;
        invalidateRequests();
        user.value = null;
        sessionHidden.value = false;
        showRegistration(false);
        registerUsername.value = '';
        loginMessage.value = message;
        loginTone.value = tone;
        onSignedOut();
    }

    async function signedIn(account: AuthUser) {
        invalidateRequests(account);
        user.value = account;
        password.value = '';
        loginMessage.value = '';
        await onAuthenticated(account);
    }

    async function login() {
        if (starting.value || user.value || busy.value) return;
        const revision = authRevision;
        const tabChange = externalAuthChange;
        pending.value = 'login';
        loginMessage.value = '';
        loginTone.value = 'error';
        try {
            const result = await loginAPI(username.value.trim(), password.value);
            notifyOtherTabs();
            if (disposed || revision !== authRevision) return;
            if (tabChange === externalAuthChange) await signedIn(result.user);
            else signedOut(authChangeMessage);
        } catch (reason) {
            if (!disposed && revision === authRevision) {
                loginMessage.value = reason instanceof Error ? reason.message : '登录失败，请重试';
            }
        } finally {
            pending.value = null;
            password.value = '';
        }
    }

    async function register(): Promise<'username' | 'password' | 'confirmation' | 'registered' | undefined> {
        if (starting.value || user.value || !registrationOpen.value || busy.value) return;
        const chosenUsername = registerUsername.value.trim();
        if (chosenUsername.toLowerCase() === 'admin') {
            registrationMessage.value = 'admin 是保留用户名';
            return 'username';
        }
        const length = [...registerPassword.value].length;
        const {password_min_length: min, password_max_length: max} = inputLimits;
        if (length < min || length > max) {
            registrationMessage.value = `密码须为 ${min}–${max} 个字符`;
            return 'password';
        }
        if (registerPassword.value !== registerConfirmation.value) {
            registrationMessage.value = '两次输入的密码不一致';
            return 'confirmation';
        }
        const revision = authRevision;
        pending.value = 'register';
        registrationMessage.value = '';
        try {
            const {user: account} = await registerAPI(chosenUsername, registerPassword.value, registerConfirmation.value);
            if (disposed || revision !== authRevision) return;
            showRegistration(false);
            registerUsername.value = '';
            username.value = account.username;
            loginMessage.value = '注册成功，请登录';
            loginTone.value = 'success';
            return 'registered';
        } catch (reason) {
            if (!disposed && revision === authRevision) {
                registrationMessage.value = reason instanceof Error ? reason.message : '注册失败，请重试';
            }
        } finally {
            pending.value = null;
        }
    }

    async function logout() {
        const revision = authRevision;
        if (!user.value || busy.value || sessionHidden.value || logoutConfirmation === revision) return;
        logoutConfirmation = revision;
        try {
            const confirmation = await confirmAction({
                title: '确认退出登录？',
                description: '退出后需要重新登录才能继续聊天。',
                action: '退出登录',
                variant: 'logout'
            });
            if (confirmation === null || disposed || revision !== authRevision || busy.value || sessionHidden.value) return;
        } finally {
            if (logoutConfirmation === revision) logoutConfirmation = undefined;
        }
        pending.value = 'logout';
        try {
            await logoutAPI();
            notifyOtherTabs();
            if (!disposed && revision === authRevision) signedOut();
        } catch (reason) {
            if (disposed || revision !== authRevision) return;
            if (reason instanceof AuthRequiredError) signedOut(expiredMessage);
            else showAlert(reason instanceof Error ? reason.message : '退出失败，请重试');
        } finally {
            pending.value = null;
        }
    }

    function syncTabs(event: StorageEvent) {
        if (event.key !== authChangeKey || event.newValue === null) return;
        externalAuthChange++;
        if (user.value) signedOut(authChangeMessage);
    }

    function hideCachedSession(event: PageTransitionEvent) {
        if (!event.persisted || !user.value) return;
        restoreCheck++;
        sessionHidden.value = true;
    }

    async function restoreCachedSession(event: PageTransitionEvent) {
        const account = user.value;
        if (!event.persisted || !account) return;
        sessionHidden.value = true;
        const check = ++restoreCheck;
        try {
            const {user: restored} = await sessionAPI();
            if (disposed || check !== restoreCheck || user.value !== account) return;
            if (restored?.id === account.id && restored.role === account.role) sessionHidden.value = false;
            else signedOut(authChangeMessage);
        } catch {
            if (!disposed && check === restoreCheck && user.value === account) {
                signedOut('无法检查登录状态，请重新登录');
            }
        }
    }

    function onAuthRequired() {
        if (user.value) signedOut(expiredMessage);
    }

    function onPasswordChangeStart() {
        changingPassword.value = true;
    }

    function onPasswordChangeEnd() {
        changingPassword.value = false;
    }

    function onPasswordChanged() {
        notifyOtherTabs();
        signedOut('密码已修改，请使用新密码登录', 'success');
    }

    function onAccountDeleted() {
        notifyOtherTabs();
        username.value = '';
        signedOut('账户已注销', 'success');
    }

    async function initializeSession() {
        const revision = authRevision;
        const tabChange = externalAuthChange;
        try {
            const {user: account, limits} = await sessionAPI();
            if (disposed) return;
            starting.value = false;
            if (limits) Object.assign(inputLimits, limits);
            if (revision !== authRevision) return;
            if (tabChange !== externalAuthChange) signedOut(authChangeMessage);
            else if (account) await signedIn(account);
            else signedOut();
        } catch (reason) {
            if (disposed) return;
            starting.value = false;
            if (revision !== authRevision) return;
            if (tabChange !== externalAuthChange) signedOut(authChangeMessage);
            else signedOut(reason instanceof AuthRequiredError ? '' : '无法检查登录状态，请确认后端已启动后再登录');
        }
    }

    onMounted(() => {
        window.addEventListener('storage', syncTabs);
        window.addEventListener('pagehide', hideCachedSession);
        window.addEventListener('pageshow', restoreCachedSession);
        window.addEventListener('auth-required', onAuthRequired);
        window.addEventListener('password-change-start', onPasswordChangeStart);
        window.addEventListener('password-change-end', onPasswordChangeEnd);
        window.addEventListener('password-changed', onPasswordChanged);
        window.addEventListener('account-deleted', onAccountDeleted);
        return initializeSession();
    });

    onBeforeUnmount(() => {
        disposed = true;
        window.removeEventListener('storage', syncTabs);
        window.removeEventListener('pagehide', hideCachedSession);
        window.removeEventListener('pageshow', restoreCachedSession);
        window.removeEventListener('auth-required', onAuthRequired);
        window.removeEventListener('password-change-start', onPasswordChangeStart);
        window.removeEventListener('password-change-end', onPasswordChangeEnd);
        window.removeEventListener('password-changed', onPasswordChanged);
        window.removeEventListener('account-deleted', onAccountDeleted);
        if (user.value) invalidateRequests();
    });

    return {
        user, starting, sessionHidden, busy, loggingIn, registering, loggingOut,
        username, password, registerUsername, registerPassword, registerConfirmation,
        registrationOpen, loginMessage, loginTone, registrationMessage, switchForm, login, register, logout
    };
}
