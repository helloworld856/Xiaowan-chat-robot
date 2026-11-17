import {AuthRequiredError, invalidateRequests, loginAPI, logoutAPI, registerAPI, sessionAPI} from './api.ts';
import type {AuthUser} from './api.ts';
import {lockSendBtn, showAlert} from './ui.ts';

const authChangeKey = 'xiaowan.auth-changed';
const authChangeMessage = '此浏览器的登录状态已更改，请重新登录';

function notifyOtherTabs() {
    try {
        // 只发送变化信号，用户身份仍由服务端根据 Cookie 校验。
        localStorage.setItem(authChangeKey, `${Date.now()}:${Math.random()}`);
    } catch {
        // 存储不可用时，私有请求仍由服务端校验当前账号。
    }
}

export async function initializeAuth(onAuthenticated: () => Promise<void>, onSignedOut: () => void) {
    const screen = document.getElementById('login-screen')!;
    const app = document.getElementById('app-shell')!;
    const loading = document.getElementById('loading')!;
    const form = document.getElementById('login-form') as HTMLFormElement;
    const username = document.getElementById('login-username') as HTMLInputElement;
    const password = document.getElementById('login-password') as HTMLInputElement;
    const button = document.getElementById('login-submit') as HTMLButtonElement;
    const error = document.getElementById('login-error')!;
    const registration = document.getElementById('register-form') as HTMLFormElement;
    const registerUsername = document.getElementById('register-username') as HTMLInputElement;
    const registerPassword = document.getElementById('register-password') as HTMLInputElement;
    const registerConfirmation = document.getElementById('register-confirm-password') as HTMLInputElement;
    const registerButton = document.getElementById('register-submit') as HTMLButtonElement;
    const registerError = document.getElementById('register-error')!;
    const openRegister = document.getElementById('open-register') as HTMLButtonElement;
    const openLogin = document.getElementById('open-login') as HTMLButtonElement;
    const logout = document.getElementById('logout-btn') as HTMLButtonElement;
    const logoutDialog = document.getElementById('logout-dialog') as HTMLDialogElement;
    const logoutCancel = document.getElementById('logout-cancel') as HTMLButtonElement;
    const logoutConfirm = document.getElementById('logout-confirm') as HTMLButtonElement;
    let busy = false;
    let externalAuthChange = 0;
    let activeUser: AuthUser | undefined;

    function showRegister(show: boolean) {
        form.hidden = show;
        registration.hidden = !show;
        password.value = '';
        registerPassword.value = '';
        registerConfirmation.value = '';
        registerError.textContent = '';
        screen.setAttribute('aria-labelledby', show ? 'register-title' : 'login-title');
        document.title = show ? '注册 · 小晚' : '登录 · 小晚';
    }

    // 先停止当前账号的请求，再清空页面上的私有内容。
    function signedOut(message = '') {
        if (logoutDialog.open) logoutDialog.close();
        activeUser = undefined;
        invalidateRequests();
        app.hidden = true;
        app.inert = true;
        screen.hidden = false;
        loading.classList.add('close');
        lockSendBtn();
        showRegister(false);
        registerUsername.value = '';
        error.textContent = message;
        error.setAttribute('data-tone', 'error');
        document.querySelectorAll('#app-shell .open, #app-shell .active, #app-shell .closing')
            .forEach(element => element.classList.remove('open', 'active', 'closing'));
        document.getElementById('chat-review')!.inert = true;
        document.querySelector('.sidebar-btn')!.setAttribute('aria-expanded', 'false');
        document.getElementById('menuBtn')!.setAttribute('aria-expanded', 'false');
        onSignedOut();
        (username.value ? password : username).focus();
    }

    async function signedIn(user: AuthUser) {
        invalidateRequests(user);
        activeUser = user;
        document.getElementById('current-username')!.textContent = user.username;
        screen.hidden = true;
        app.hidden = false;
        app.inert = false;
        password.value = '';
        error.textContent = '';
        await onAuthenticated();
    }

    openRegister.addEventListener('click', () => {
        if (busy) return;
        error.textContent = '';
        showRegister(true);
        registerUsername.focus();
    });
    openLogin.addEventListener('click', () => {
        if (busy) return;
        showRegister(false);
        username.focus();
    });
    registration.addEventListener('submit', async event => {
        event.preventDefault();
        if (busy || !registration.reportValidity()) return;
        const chosenUsername = registerUsername.value.trim();
        if (chosenUsername.toLowerCase() === 'admin') {
            registerError.textContent = 'admin 是保留用户名';
            registerUsername.focus();
            return;
        }
        const length = [...registerPassword.value].length;
        if (length < 6 || length > 15) {
            registerError.textContent = '密码须为 6–15 个字符';
            registerPassword.focus();
            return;
        }
        if (registerPassword.value !== registerConfirmation.value) {
            registerError.textContent = '两次输入的密码不一致';
            registerConfirmation.focus();
            return;
        }
        busy = true;
        registerButton.disabled = true;
        openLogin.disabled = true;
        registerButton.textContent = '注册中…';
        registerError.textContent = '';
        try {
            const {user} = await registerAPI(chosenUsername, registerPassword.value, registerConfirmation.value);
            showRegister(false);
            registerUsername.value = '';
            username.value = user.username;
            error.textContent = '注册成功，请登录';
            error.setAttribute('data-tone', 'success');
            password.focus();
        } catch (reason) {
            registerError.textContent = reason instanceof Error ? reason.message : '注册失败，请重试';
        } finally {
            busy = false;
            registerButton.disabled = false;
            openLogin.disabled = false;
            registerButton.textContent = '注册';
        }
    });

    window.addEventListener('storage', event => {
        if (event.key !== authChangeKey || event.newValue === null) return;
        externalAuthChange++;
        // 登录页没有私有状态，后台标签的变更不清空正在填写的表单。
        if (!app.hidden || screen.hidden) signedOut(authChangeMessage);
    });
    // 页面进入前进后退缓存时先藏起聊天，恢复后验证 Cookie 再显示。
    window.addEventListener('pagehide', event => {
        if (event.persisted && activeUser) app.hidden = true;
    });
    window.addEventListener('pageshow', async event => {
        const previousUser = activeUser;
        if (!event.persisted || !previousUser) return;
        try {
            const {user} = await sessionAPI();
            if (activeUser !== previousUser) return;
            if (user?.id === previousUser.id && user?.role === previousUser.role) app.hidden = false;
            else signedOut(authChangeMessage);
        } catch {
            if (activeUser === previousUser) signedOut('无法检查登录状态，请重新登录');
        }
    });
    window.addEventListener('auth-required', () => signedOut('登录已过期，请重新登录'));
    window.addEventListener('password-change-start', () => {
        busy = true;
        button.disabled = true;
        logout.disabled = true;
    });
    window.addEventListener('password-change-end', () => {
        busy = false;
        button.disabled = false;
        logout.disabled = false;
    });
    window.addEventListener('password-changed', () => {
        notifyOtherTabs();
        signedOut('密码已修改，请使用新密码登录');
        error.setAttribute('data-tone', 'success');
    });
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (busy || !form.reportValidity()) return;
        const attempt = externalAuthChange;
        busy = true;
        button.disabled = true;
        button.textContent = '登录中…';
        error.textContent = '';
        error.setAttribute('data-tone', 'error');
        try {
            const result = await loginAPI(username.value.trim(), password.value);
            notifyOtherTabs();
            if (attempt === externalAuthChange) await signedIn(result.user);
            else signedOut(authChangeMessage);
        } catch (reason) {
            error.textContent = reason instanceof Error ? reason.message : '登录失败，请重试';
        } finally {
            busy = false;
            password.value = '';
            button.disabled = false;
            button.textContent = '登录';
        }
    });
    logout.addEventListener('click', () => {
        if (busy || logout.disabled || logoutDialog.open) return;
        logoutDialog.showModal();
        logoutCancel.focus();
    });
    logoutCancel.addEventListener('click', () => logoutDialog.close());
    logoutConfirm.addEventListener('click', async () => {
        if (busy || logout.disabled || !logoutDialog.open) return;
        logoutDialog.close();
        busy = true;
        logout.disabled = true;
        logout.textContent = '退出中…';
        button.disabled = true;
        button.textContent = '正在退出…';
        try {
            await logoutAPI();
            notifyOtherTabs();
            signedOut();
        } catch (reason) {
            const message = reason instanceof Error ? reason.message : '退出失败，请重试';
            if (reason instanceof AuthRequiredError) signedOut('登录已过期，请重新登录');
            else if (screen.hidden) showAlert(message);
            else error.textContent = message;
        } finally {
            busy = false;
            logout.disabled = false;
            logout.textContent = '退出登录';
            button.disabled = false;
            button.textContent = '登录';
        }
    });

    const startup = externalAuthChange;
    try {
        const result = await sessionAPI();
        if (startup !== externalAuthChange) return;
        if (result.user) await signedIn(result.user);
        else signedOut();
    } catch (reason) {
        if (startup !== externalAuthChange) return;
        signedOut(reason instanceof AuthRequiredError ? '' : '无法检查登录状态，请确认后端已启动后再登录');
    }
}
