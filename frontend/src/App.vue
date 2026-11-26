<script setup lang="ts">
import {computed, onBeforeUnmount, onMounted, ref} from 'vue';
import AccountMenu from './components/AccountMenu.vue';
import AppDialogs from './components/AppDialogs.vue';
import AuthScreen from './components/AuthScreen.vue';
import ChatWorkspace from './components/ChatWorkspace.vue';
import ProfileDialog from './components/ProfileDialog.vue';
import SettingsDialog from './components/SettingsDialog.vue';
import {useSession} from './composables/useSession.ts';
import {AuthRequiredError, CancelledRequestError, getRequestGeneration, personaAPI} from './ts/api.ts';
import type {ProfileInfo} from './ts/api.ts';
import type {ConfirmOptions} from './ts/dialogs.ts';
import type {PersonaInfo} from './ts/global_config.ts';
import {darkSchemeQuery, resolveTheme} from './ts/preferences.ts';
import type {Preferences} from './ts/preferences.ts';

const menu = ref<InstanceType<typeof AccountMenu> | null>(null);
const dialogs = ref<InstanceType<typeof AppDialogs> | null>(null);
const authScreen = ref<InstanceType<typeof AuthScreen> | null>(null);
const chat = ref<InstanceType<typeof ChatWorkspace> | null>(null);
const settings = ref<InstanceType<typeof SettingsDialog> | null>(null);
const profileDialog = ref<InstanceType<typeof ProfileDialog> | null>(null);
const defaultPreferences: Preferences = {send_shortcut: 'enter', theme: 'theme0', chat_font_size: 'standard'};
const preferences = ref<Preferences>({...defaultPreferences});
const profile = ref<ProfileInfo | null>(null);
const persona = ref<PersonaInfo | null>(null);
const ready = ref(false);
const chatBusy = ref(false);
const clearingHistory = ref(false);

const session = useSession({onAuthenticated: initialize, onSignedOut: resetAccount, confirmAction, showAlert});
const {user, starting, sessionHidden, busy: sessionBusy, loggingOut} = session;
const isAdmin = computed(() => user.value?.role === 'admin');
const loading = computed(() => starting.value || (user.value !== null && !ready.value));

function showAlert(message: string) {
    dialogs.value?.showAlert(message);
}

function confirmAction(options: ConfirmOptions) {
    return dialogs.value?.confirmAction(options) ?? Promise.resolve(null);
}

function applyPreferences(saved: Preferences) {
    preferences.value = {...saved};
    applyTheme();
}

// 「跟随系统」时监听系统配色变化，其他主题不受影响。
const darkScheme = typeof globalThis.matchMedia === 'function' ? globalThis.matchMedia(darkSchemeQuery) : null;
function applyTheme() {
    document.documentElement.className = resolveTheme(preferences.value.theme, darkScheme?.matches ?? false);
}
onMounted(() => darkScheme?.addEventListener('change', applyTheme));
onBeforeUnmount(() => darkScheme?.removeEventListener('change', applyTheme));

async function loadPersona() {
    const generation = getRequestGeneration();
    const saved = await personaAPI();
    if (generation !== getRequestGeneration()) throw new CancelledRequestError();
    persona.value = saved;
    if (saved.BOT_NAME) document.title = saved.BOT_NAME;
}

async function initialize() {
    const generation = getRequestGeneration();
    ready.value = false;
    // 资料先于人设和历史加载，其他步骤失败时仍可初始化可用的部分。
    const steps = [
        {name: '个人资料', load: () => profileDialog.value!.load()},
        {name: '角色信息', load: loadPersona},
        {name: '应用设置', load: () => settings.value!.load()},
        {name: '历史对话', load: () => chat.value!.loadHistory()},
    ];
    const warnings: string[] = [];
    try {
        for (const step of steps) {
            if (generation !== getRequestGeneration()) return;
            try {
                await step.load();
            } catch (error) {
                if (generation !== getRequestGeneration() || error instanceof AuthRequiredError || error instanceof CancelledRequestError) return;
                console.warn(`${step.name}加载失败`, error);
                warnings.push(`${step.name}加载失败，请检查后端、网络或相关配置。`);
            }
        }
        if (generation === getRequestGeneration() && warnings.length) showAlert(warnings.join('\n'));
    } finally {
        if (generation === getRequestGeneration()) ready.value = true;
    }
}

function resetAccount() {
    ready.value = false;
    chatBusy.value = false;
    clearingHistory.value = false;
    dialogs.value?.reset();
    menu.value?.reset();
    profileDialog.value?.reset();
    settings.value?.reset();
    chat.value?.reset();
    profile.value = null;
    persona.value = null;
    applyPreferences(defaultPreferences);
}
</script>

<template>
    <div id="loading" :class="{close: !loading}">
        <div class="typing-indicator">
            <div v-for="index in 3" :key="`circle-${index}`" class="typing-circle"></div>
            <div v-for="index in 3" :key="`shadow-${index}`" class="typing-shadow"></div>
        </div>
    </div>
    <AuthScreen v-if="!starting && !user && !sessionHidden" ref="authScreen" :session="session" />
    <div id="app-shell" :data-chat-font-size="preferences.chat_font_size"
        :hidden="!user || sessionHidden" :inert="!ready || sessionHidden || !user">
        <ChatWorkspace ref="chat" :ready="ready" :shortcut="preferences.send_shortcut" :profile="profile"
            :persona="persona" :clearing-history="clearingHistory" :show-alert="showAlert"
            :confirm-action="confirmAction" @busy-change="chatBusy = $event" />
        <AccountMenu ref="menu" :display-name="profile?.nickname ?? user?.username ?? ''" :logout-busy="sessionBusy" :logging-out="loggingOut"
            @profile="profileDialog?.open()" @settings="settings?.open()" @logout="session.logout()" />
        <ProfileDialog ref="profileDialog" :is-admin="isAdmin" :show-alert="showAlert" :confirm-action="confirmAction"
            @profile-change="profile = $event" @close="user && menu?.focus()" />
        <SettingsDialog ref="settings" :is-admin="isAdmin" :preferences="preferences" :chat-busy="chatBusy"
            :show-alert="showAlert" :confirm-action="confirmAction" @preferences-change="applyPreferences"
            @clearing-history="clearingHistory = $event" @history-cleared="chat?.clearHistory()" />
    </div>
    <div :hidden="sessionHidden">
        <AppDialogs ref="dialogs" @alert-closed="authScreen?.restoreFocus()" />
    </div>
</template>
