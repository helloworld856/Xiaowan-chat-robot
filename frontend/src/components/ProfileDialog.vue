<script setup lang="ts">
import {computed, nextTick, ref} from 'vue';
import {AuthRequiredError, CancelledRequestError, changePasswordAPI, deleteAccountAPI, getRequestGeneration, profileAPI} from '../ts/api.ts';
import type {AvatarId, ProfileInfo} from '../ts/api.ts';
import type {ConfirmOptions} from '../ts/dialogs.ts';
import {inputLimits} from '../ts/global_config.ts';

const props = defineProps<{
    isAdmin: boolean;
    showAlert: (message: string) => void;
    confirmAction: (options: ConfirmOptions) => Promise<string | null>;
}>();
const emit = defineEmits<{'profile-change': [profile: ProfileInfo]; close: []}>();

const dialog = ref<HTMLDialogElement | null>(null);
const nicknameInput = ref<HTMLInputElement | null>(null);
const currentPasswordInput = ref<HTMLInputElement | null>(null);
const newPasswordInput = ref<HTMLInputElement | null>(null);
const confirmPasswordInput = ref<HTMLInputElement | null>(null);
const profile = ref<ProfileInfo | null>(null);
const busy = ref(false);
const ready = ref(false);
const editingPassword = ref(false);
const nickname = ref('');
const avatarId = ref<AvatarId>('cat');
const preview = computed(() => profile.value?.avatars.find(avatar => avatar.id === avatarId.value)?.url ?? profile.value?.avatar_url);

function clearPasswords() {
    for (const field of [currentPasswordInput.value, newPasswordInput.value, confirmPasswordInput.value]) {
        if (field) field.value = '';
    }
}

function showPassword(show: boolean) {
    editingPassword.value = show;
    clearPasswords();
    if (show) void nextTick(() => currentPasswordInput.value?.focus());
}

function requestIsCurrent(generation: number, error?: unknown) {
    return generation === getRequestGeneration() && !(error instanceof AuthRequiredError || error instanceof CancelledRequestError);
}

function applyProfile(saved: ProfileInfo) {
    profile.value = saved;
    emit('profile-change', saved);
}

async function load() {
    const generation = getRequestGeneration();
    const saved = await profileAPI();
    if (!requestIsCurrent(generation)) throw new CancelledRequestError();
    applyProfile(saved);
    return saved;
}

async function open() {
    if (!dialog.value || dialog.value.open || busy.value) return;
    const generation = getRequestGeneration();
    showPassword(false);
    ready.value = false;
    busy.value = true;
    try {
        await nextTick();
        if (!dialog.value || !requestIsCurrent(generation)) return;
        dialog.value.showModal();
        const saved = await load();
        if (!requestIsCurrent(generation)) return;
        nickname.value = saved.nickname;
        avatarId.value = saved.avatar_id;
        ready.value = true;
    } catch (error) {
        if (requestIsCurrent(generation, error)) {
            props.showAlert(error instanceof Error ? error.message : '读取失败，请关闭后重试');
        }
    } finally {
        if (requestIsCurrent(generation)) busy.value = false;
    }
}

function close() {
    if (!busy.value && dialog.value?.open) dialog.value.close();
}

function onClose() {
    clearPasswords();
    emit('close');
}

function reset() {
    if (dialog.value?.open) dialog.value.close();
    profile.value = null;
    ready.value = false;
    busy.value = false;
    nickname.value = '';
    avatarId.value = 'cat';
    showPassword(false);
}

async function save() {
    if (busy.value || !ready.value || !profile.value) return;
    nickname.value = nicknameInput.value?.value ?? nickname.value;
    const name = nickname.value.trim();
    if (!name) {
        nicknameInput.value?.focus();
        props.showAlert('请输入昵称');
        return;
    }
    if ([...name].length > inputLimits.max_nickname_chars) {
        nicknameInput.value?.focus();
        props.showAlert(`昵称最多为 ${inputLimits.max_nickname_chars} 个字符`);
        return;
    }
    const generation = getRequestGeneration();
    busy.value = true;
    let feedback = '个人资料已保存。';
    try {
        const saved = await profileAPI({nickname: name, avatar_id: avatarId.value});
        if (!requestIsCurrent(generation)) return;
        applyProfile(saved);
        dialog.value?.close();
    } catch (error) {
        if (!requestIsCurrent(generation, error)) return;
        feedback = error instanceof Error ? error.message : '保存失败，请重试';
    } finally {
        if (requestIsCurrent(generation)) busy.value = false;
    }
    props.showAlert(feedback);
}

async function deleteAccount() {
    if (busy.value || !ready.value) return;
    if (props.isAdmin) {
        props.showAlert('内置管理员账号不能注销。');
        return;
    }
    const generation = getRequestGeneration();
    const password = await props.confirmAction({
        title: '确认注销账户？',
        description: '账户、聊天记录、个人资料和模型配置将永久删除，且无法恢复。',
        action: '注销账户', password: true, destructive: true,
    });
    if (password === null || !requestIsCurrent(generation)) return;
    busy.value = true;
    window.dispatchEvent(new Event('credential-change-start'));
    try {
        await deleteAccountAPI(password);
        window.dispatchEvent(new Event('account-deleted'));
    } catch (error) {
        if (requestIsCurrent(generation, error)) {
            props.showAlert(error instanceof Error ? error.message : '注销账户失败，请稍后重试');
        }
    } finally {
        if (requestIsCurrent(generation)) busy.value = false;
        window.dispatchEvent(new Event('credential-change-end'));
    }
}

async function savePassword() {
    if (busy.value || !ready.value) return;
    const passwords = {
        current_password: currentPasswordInput.value?.value ?? '',
        new_password: newPasswordInput.value?.value ?? '',
        confirm_password: confirmPasswordInput.value?.value ?? '',
    };
    if (!passwords.current_password) {
        currentPasswordInput.value?.focus();
        props.showAlert('请输入当前密码');
        return;
    }
    if (!passwords.new_password) {
        newPasswordInput.value?.focus();
        props.showAlert('请输入新密码');
        return;
    }
    const length = [...passwords.new_password].length;
    const {password_min_length: min, password_max_length: max} = inputLimits;
    if (length < min || length > max) {
        newPasswordInput.value?.focus();
        props.showAlert(`新密码须为 ${min}–${max} 个字符`);
        return;
    }
    if (!passwords.confirm_password) {
        confirmPasswordInput.value?.focus();
        props.showAlert('请确认新密码');
        return;
    }
    if (passwords.new_password !== passwords.confirm_password) {
        confirmPasswordInput.value?.focus();
        props.showAlert('两次新密码不一致');
        return;
    }
    const generation = getRequestGeneration();
    busy.value = true;
    window.dispatchEvent(new Event('credential-change-start'));
    try {
        await changePasswordAPI(passwords);
        if (requestIsCurrent(generation)) dialog.value?.close();
        window.dispatchEvent(new Event('password-changed'));
    } catch (error) {
        if (requestIsCurrent(generation, error)) {
            props.showAlert(error instanceof Error ? error.message : '修改失败，请重试');
        }
    } finally {
        if (requestIsCurrent(generation)) {
            clearPasswords();
            busy.value = false;
        }
        window.dispatchEvent(new Event('credential-change-end'));
    }
}

defineExpose({open, load, reset});
</script>

<template>
    <dialog id="profile-dialog" ref="dialog" aria-labelledby="profile-title" :aria-busy="busy" @cancel.prevent="close" @close="onClose">
        <div class="profile-heading">
            <h2 id="profile-title">{{ editingPassword ? '修改密码' : '个人资料' }}</h2>
            <button id="profile-close" class="profile-text-button" type="button" :disabled="busy" @click="close">关闭</button>
        </div>
        <form id="profile-form" novalidate :hidden="editingPassword" @submit.prevent="save">
            <fieldset id="profile-fields" :disabled="busy || !ready">
                <div class="profile-summary">
                    <img id="profile-preview" :src="preview" alt="当前头像" width="60" height="60">
                    <div>
                        <strong id="profile-display-name">{{ nickname.trim() || profile?.username }}</strong>
                        <p class="profile-account">登录账号：<span id="profile-username">{{ profile?.username }}</span></p>
                    </div>
                </div>
                <label class="profile-label" for="profile-nickname">昵称</label>
                <input id="profile-nickname" ref="nicknameInput" v-model="nickname" v-memo="[nickname]"
                    class="profile-input" required autocomplete="nickname">
                <div class="profile-avatar-heading">
                    <span class="profile-label">系统头像</span>
                    <button id="profile-default-avatar" class="profile-text-button" type="button" @click="avatarId = profile?.default_avatar_id ?? 'cat'">恢复默认</button>
                </div>
                <div id="profile-avatars" role="group" aria-label="系统头像">
                    <label v-for="avatar in profile?.avatars" :key="avatar.id" class="avatar-choice"
                        :title="avatar.name + (avatar.id === profile?.default_avatar_id ? '（默认）' : '')">
                        <input v-model="avatarId" type="radio" name="avatar_id" :value="avatar.id" required>
                        <img :src="avatar.url" alt="" width="40" height="40">
                        <span>{{ avatar.name }}</span>
                    </label>
                </div>
                <button id="profile-open-password" class="profile-password-link" type="button" @click="showPassword(true)">修改密码 <span aria-hidden="true">›</span></button>
                <div class="profile-account-actions">
                    <button id="profile-delete-account" class="danger-button" type="button" @click="deleteAccount">注销账户</button>
                </div>
            </fieldset>
            <div class="profile-actions">
                <button id="profile-save" class="profile-primary" type="submit" :disabled="busy || !ready">保存</button>
            </div>
        </form>
        <form id="profile-password-form" method="post" novalidate :hidden="!editingPassword" @submit.prevent="savePassword">
            <fieldset id="profile-password-fields" :disabled="busy">
                <label class="profile-label" for="profile-current-password">当前密码</label>
                <input id="profile-current-password" ref="currentPasswordInput" class="profile-input"
                    name="current_password" type="password" autocomplete="current-password" maxlength="512" required>
                <label class="profile-label" for="profile-new-password">新密码</label>
                <input id="profile-new-password" ref="newPasswordInput" class="profile-input" name="new_password" type="password"
                    autocomplete="new-password" required>
                <label class="profile-label" for="profile-confirm-password">确认新密码</label>
                <input id="profile-confirm-password" ref="confirmPasswordInput" class="profile-input" name="confirm_password" type="password"
                    autocomplete="new-password" required>
            </fieldset>
            <div class="profile-actions">
                <button id="profile-password-back" class="profile-secondary" type="button" :disabled="busy" @click="showPassword(false)">返回资料</button>
                <button id="profile-password-save" class="profile-primary" type="submit" :disabled="busy">修改并重新登录</button>
            </div>
        </form>
    </dialog>
</template>
