<script setup lang="ts">
import {nextTick, onMounted, ref, watch} from 'vue';
import type {useSession} from '../composables/useSession.ts';

const {session} = defineProps<{session: ReturnType<typeof useSession>}>();
const {
    user, starting, busy, loggingIn, registering, loggingOut,
    username, password, registerUsername, registerPassword, registerConfirmation,
    registrationOpen, switchForm, login, register
} = session;

const loginUsernameInput = ref<HTMLInputElement | null>(null);
const loginPasswordInput = ref<HTMLInputElement | null>(null);
const registerUsernameInput = ref<HTMLInputElement | null>(null);
const registerPasswordInput = ref<HTMLInputElement | null>(null);
const registerConfirmationInput = ref<HTMLInputElement | null>(null);
let pendingFocus: typeof registerUsernameInput | undefined;

function focusLogin() {
    if (user.value || starting.value) return;
    const input = username.value.trim() ? loginPasswordInput : loginUsernameInput;
    input.value?.focus();
}

function restoreFocus() {
    const input = pendingFocus;
    pendingFocus = undefined;
    if (input) input.value?.focus();
    else if (registrationOpen.value) registerUsernameInput.value?.focus();
    else focusLogin();
}

onMounted(focusLogin);
watch([user, starting], focusLogin, {flush: 'post'});
watch([user, starting, registrationOpen], ([account, pending, showRegistration]) => {
    if (!account && !pending) document.title = showRegistration ? '注册 · 小晚' : '登录 · 小晚';
}, {immediate: true});
watch(registrationOpen, () => {
    // 密码管理器可能只改了 DOM，切换表单时也要清空这些值。
    for (const input of [loginPasswordInput, registerPasswordInput, registerConfirmationInput]) {
        if (input.value) input.value.value = '';
    }
});

async function changeForm(showRegistration: boolean) {
    if (!switchForm(showRegistration)) return;
    pendingFocus = undefined;
    await nextTick();
    const input = showRegistration ? registerUsernameInput : loginUsernameInput;
    input.value?.focus();
}

function submitLogin() {
    if (busy.value) return;
    // 密码管理器可以直接填充 DOM，提交时同时读取当前输入值。
    username.value = loginUsernameInput.value!.value;
    password.value = loginPasswordInput.value!.value;
    return login();
}

async function submitRegistration() {
    if (busy.value) return;
    registerUsername.value = registerUsernameInput.value!.value;
    registerPassword.value = registerPasswordInput.value!.value;
    registerConfirmation.value = registerConfirmationInput.value!.value;
    const field = await register();
    if (!field) return;
    const inputs = {
        username: registerUsernameInput,
        password: registerPasswordInput,
        confirmation: registerConfirmationInput,
        registered: loginPasswordInput
    };
    pendingFocus = inputs[field];
}

defineExpose({restoreFocus});
</script>

<template>
    <section id="login-screen" :aria-labelledby="registrationOpen ? 'register-title' : 'login-title'">
        <!-- 输入框只随自身绑定值更新，避免其他状态的重新渲染清除自动填充。 -->
        <form id="login-form" class="login-card" method="post" novalidate :hidden="registrationOpen" @submit.prevent="submitLogin">
            <header class="login-heading">
                <h1 id="login-title">登录小晚</h1>
            </header>
            <div class="login-fields">
                <div class="login-field">
                    <label for="login-username">用户名</label>
                    <input id="login-username" ref="loginUsernameInput" v-model="username" v-memo="[username]" name="username" type="text"
                           autocomplete="username" maxlength="64" required>
                </div>
                <div class="login-field">
                    <label for="login-password">密码</label>
                    <input id="login-password" ref="loginPasswordInput" v-model="password" v-memo="[password]" name="password" type="password"
                           autocomplete="current-password" maxlength="512" required>
                </div>
            </div>
            <button id="login-submit" class="login-primary" type="submit" :disabled="busy">
                {{ loggingIn ? '登录中…' : loggingOut ? '正在退出…' : '登录' }}
            </button>
            <button id="open-register" class="login-switch" type="button" :disabled="busy" @click="changeForm(true)">
                注册账号
            </button>
        </form>
        <form id="register-form" class="login-card" method="post" novalidate :hidden="!registrationOpen" @submit.prevent="submitRegistration">
            <header class="login-heading"><h1 id="register-title">注册账号</h1></header>
            <div class="login-fields">
                <div class="login-field">
                    <label for="register-username">用户名</label>
                    <input id="register-username" ref="registerUsernameInput" v-model="registerUsername" v-memo="[registerUsername]" name="username"
                           type="text" autocomplete="username" maxlength="32" required>
                </div>
                <div class="login-field">
                    <label for="register-password">密码</label>
                    <input id="register-password" ref="registerPasswordInput" v-model="registerPassword" v-memo="[registerPassword]" name="password"
                           type="password" autocomplete="new-password" required>
                </div>
                <div class="login-field">
                    <label for="register-confirm-password">确认密码</label>
                    <input id="register-confirm-password" ref="registerConfirmationInput" v-model="registerConfirmation" v-memo="[registerConfirmation]"
                           name="confirm_password" type="password" autocomplete="new-password" required>
                </div>
            </div>
            <button id="register-submit" class="login-primary" type="submit" :disabled="busy">
                {{ registering ? '注册中…' : '注册' }}
            </button>
            <button id="open-login" class="login-switch" type="button" :disabled="busy" @click="changeForm(false)">
                返回登录
            </button>
        </form>
    </section>
</template>
