import {AuthRequiredError, invalidateRequests, loginAPI, logoutAPI, sessionAPI} from './api.ts';
import type {AuthUser} from './api.ts';
import {lockSendBtn, showAlert} from './ui.ts';

export async function initializeAuth(onAuthenticated: () => Promise<void>, onSignedOut: () => void) {
    const screen = document.getElementById('login-screen')!;
    const app = document.getElementById('app-shell')!;
    const loading = document.getElementById('loading')!;
    const form = document.getElementById('login-form') as HTMLFormElement;
    const username = document.getElementById('login-username') as HTMLInputElement;
    const password = document.getElementById('login-password') as HTMLInputElement;
    const button = document.getElementById('login-submit') as HTMLButtonElement;
    const error = document.getElementById('login-error')!;
    const logout = document.getElementById('logout-btn') as HTMLButtonElement;
    const logoutDialog = document.getElementById('logout-dialog') as HTMLDialogElement;
    const logoutCancel = document.getElementById('logout-cancel') as HTMLButtonElement;
    const logoutConfirm = document.getElementById('logout-confirm') as HTMLButtonElement;
    let busy = false;

    function signedOut(message = '') {
        if (logoutDialog.open) logoutDialog.close();
        invalidateRequests();
        app.hidden = true;
        app.inert = true;
        screen.hidden = false;
        loading.classList.add('close');
        lockSendBtn();
        password.value = '';
        error.textContent = message;
        error.setAttribute('data-tone', 'error');
        document.title = '登录 · 小晚';
        document.querySelectorAll('#app-shell .open, #app-shell .active, #app-shell .closing')
            .forEach(element => element.classList.remove('open', 'active', 'closing'));
        document.getElementById('chat-review')!.inert = true;
        document.querySelector('.sidebar-btn')!.setAttribute('aria-expanded', 'false');
        onSignedOut();
        password.focus();
    }

    async function signedIn(user: AuthUser) {
        invalidateRequests();
        document.getElementById('current-username')!.textContent = user.username;
        screen.hidden = true;
        app.hidden = false;
        app.inert = false;
        password.value = '';
        error.textContent = '';
        await onAuthenticated();
    }

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
        signedOut('密码已修改，请使用新密码登录');
        error.setAttribute('data-tone', 'success');
    });
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (busy || !form.reportValidity()) return;
        busy = true;
        button.disabled = true;
        button.textContent = '登录中…';
        error.textContent = '';
        try {
            const result = await loginAPI(username.value.trim(), password.value);
            await signedIn(result.user);
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
            signedOut();
        } catch (reason) {
            const message = reason instanceof Error ? reason.message : '退出失败，请重试';
            if (screen.hidden) showAlert(message);
            else error.textContent = message;
        } finally {
            busy = false;
            logout.disabled = false;
            logout.textContent = '退出登录';
            button.disabled = false;
            button.textContent = '登录';
        }
    });

    try {
        const result = await sessionAPI();
        if (result.user) await signedIn(result.user);
        else signedOut();
    } catch (reason) {
        signedOut(reason instanceof AuthRequiredError ? '' : '无法检查登录状态，请确认后端已启动后再登录');
    }
}
