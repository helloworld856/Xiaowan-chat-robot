<script setup lang="ts">
import {nextTick, onMounted, ref, watch} from 'vue';
import type {useSession} from '../composables/useSession.ts';

const {session} = defineProps<{session: ReturnType<typeof useSession>}>();
const {
    user, starting, busy, loggingIn, registering, loggingOut,
    username, password, registerUsername, registerPassword, registerConfirmation,
    registrationOpen, loginMessage, loginTone, registrationMessage, switchForm, login, register
} = session;

const loginUsernameInput = ref<HTMLInputElement | null>(null);
const loginPasswordInput = ref<HTMLInputElement | null>(null);
const registerUsernameInput = ref<HTMLInputElement | null>(null);
const registerPasswordInput = ref<HTMLInputElement | null>(null);
const registerConfirmationInput = ref<HTMLInputElement | null>(null);

function focusLogin() {
    if (user.value || starting.value) return;
    const input = username.value ? loginPasswordInput : loginUsernameInput;
    input.value?.focus();
}

onMounted(focusLogin);
watch([user, starting], focusLogin, {flush: 'post'});
watch([user, starting, registrationOpen], ([account, pending, showRegistration]) => {
    if (!account && !pending) document.title = showRegistration ? '注册 · 小晚' : '登录 · 小晚';
}, {immediate: true});

async function changeForm(showRegistration: boolean) {
    if (!switchForm(showRegistration)) return;
    await nextTick();
    const input = showRegistration ? registerUsernameInput : loginUsernameInput;
    input.value?.focus();
}

function submitLogin(event: SubmitEvent) {
    if (busy.value || !(event.currentTarget as HTMLFormElement).reportValidity()) return;
    // 密码管理器可以直接填充 DOM，提交时同时读取当前输入值。
    username.value = loginUsernameInput.value!.value;
    password.value = loginPasswordInput.value!.value;
    return login();
}

async function submitRegistration(event: SubmitEvent) {
    if (busy.value || !(event.currentTarget as HTMLFormElement).reportValidity()) return;
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
    await nextTick();
    inputs[field].value?.focus();
}
</script>

<template>
    <section id="login-screen" :aria-labelledby="registrationOpen ? 'register-title' : 'login-title'">
        <form id="login-form" class="login-card" method="post" :hidden="registrationOpen" @submit.prevent="submitLogin">
            <header class="login-heading">
                <h1 id="login-title">登录小晚</h1>
            </header>
            <div class="login-fields">
                <div class="login-field">
                    <label for="login-username">用户名</label>
                    <input id="login-username" ref="loginUsernameInput" v-model="username" name="username" type="text"
                           autocomplete="username" maxlength="64" required>
                </div>
                <div class="login-field">
                    <label for="login-password">密码</label>
                    <input id="login-password" ref="loginPasswordInput" v-model="password" name="password" type="password"
                           autocomplete="current-password" maxlength="512" aria-describedby="login-error" required>
                </div>
            </div>
            <p id="login-error" class="login-error" role="alert" :data-tone="loginTone">{{ loginMessage }}</p>
            <button id="login-submit" class="login-primary" type="submit" :disabled="busy">
                {{ loggingIn ? '登录中…' : loggingOut ? '正在退出…' : '登录' }}
            </button>
            <button id="open-register" class="login-switch" type="button" :disabled="busy" @click="changeForm(true)">
                注册账号
            </button>
        </form>
        <form id="register-form" class="login-card" method="post" :hidden="!registrationOpen" @submit.prevent="submitRegistration">
            <header class="login-heading"><h1 id="register-title">注册账号</h1></header>
            <div class="login-fields">
                <div class="login-field">
                    <label for="register-username">用户名</label>
                    <input id="register-username" ref="registerUsernameInput" v-model="registerUsername" name="username"
                           type="text" autocomplete="username" minlength="3" maxlength="32" pattern="[A-Za-z0-9_]{3,32}"
                           title="用户名须为 3–32 位英文字母、数字或下划线" required>
                </div>
                <div class="login-field">
                    <label for="register-password">密码</label>
                    <input id="register-password" ref="registerPasswordInput" v-model="registerPassword" name="password"
                           type="password" autocomplete="new-password" aria-describedby="register-error" required>
                </div>
                <div class="login-field">
                    <label for="register-confirm-password">确认密码</label>
                    <input id="register-confirm-password" ref="registerConfirmationInput" v-model="registerConfirmation"
                           name="confirm_password" type="password" autocomplete="new-password" aria-describedby="register-error" required>
                </div>
            </div>
            <p id="register-error" class="login-error" role="alert">{{ registrationMessage }}</p>
            <button id="register-submit" class="login-primary" type="submit" :disabled="busy">
                {{ registering ? '注册中…' : '注册' }}
            </button>
            <button id="open-login" class="login-switch" type="button" :disabled="busy" @click="changeForm(false)">
                返回登录
            </button>
        </form>
    </section>
</template>
