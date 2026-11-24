<script setup lang="ts">
import {nextTick, onBeforeUnmount, ref} from 'vue';
import {closeDialog, openDialog, resetDialog} from '../ts/dialogs.ts';
import type {ConfirmOptions} from '../ts/dialogs.ts';

const logout = ref<HTMLDialogElement | null>(null);
const confirmation = ref<HTMLDialogElement | null>(null);
const alert = ref<HTMLDialogElement | null>(null);
const password = ref<HTMLInputElement | null>(null);
const options = ref<ConfirmOptions | null>(null);
const message = ref('');
let pending: {dialog: HTMLDialogElement; resolve: (value: string | null) => void} | null = null;
let alertRevision = 0;

function clearPassword() {
    if (password.value) password.value.value = '';
}

function finishConfirmation(result: string | null) {
    if (!pending) return;
    const {dialog, resolve} = pending;
    pending = null;
    if (dialog.open) dialog.close();
    clearPassword();
    options.value = null;
    resolve(result);
}

function submitConfirmation() {
    finishConfirmation(options.value?.password ? password.value?.value ?? '' : '');
}

function confirmAction(next: ConfirmOptions) {
    const dialog = next.variant === 'logout' ? logout.value : confirmation.value;
    if (pending || !dialog) return Promise.resolve(null);
    clearPassword();
    options.value = next;
    return new Promise<string | null>(resolve => {
        const request = {dialog, resolve};
        pending = request;
        void nextTick(() => {
            if (pending === request) dialog.showModal();
        });
    });
}

function showAlert(text: string) {
    message.value = text;
    const revision = ++alertRevision;
    void nextTick(() => {
        if (alert.value && revision === alertRevision) openDialog(alert.value);
    });
}

function dismissAlert() {
    if (alert.value) void closeDialog(alert.value);
}

function reset() {
    finishConfirmation(null);
    alertRevision++;
    message.value = '';
    if (alert.value) resetDialog(alert.value);
}

onBeforeUnmount(reset);
defineExpose({confirmAction, showAlert, reset});
</script>

<template>
    <dialog id="logout-dialog" ref="logout" class="confirm-dialog" role="alertdialog"
        aria-labelledby="logout-title" aria-describedby="logout-description" @cancel.prevent="finishConfirmation(null)">
        <h2 id="logout-title">{{ options?.title }}</h2>
        <p id="logout-description">{{ options?.description }}</p>
        <div class="confirm-actions">
            <button id="logout-cancel" type="button" autofocus @click="finishConfirmation(null)">取消</button>
            <button id="logout-confirm" class="confirm-primary" type="button" @click="finishConfirmation('')">{{ options?.action }}</button>
        </div>
    </dialog>
    <dialog id="data-confirm-dialog" ref="confirmation" class="confirm-dialog" role="alertdialog"
        aria-labelledby="data-confirm-title" aria-describedby="data-confirm-description" @cancel.prevent="finishConfirmation(null)">
        <h2 id="data-confirm-title">{{ options?.title }}</h2>
        <p id="data-confirm-description">{{ options?.description }}</p>
        <form id="data-confirm-form" method="dialog" @submit.prevent="submitConfirmation">
            <label id="data-confirm-password-field" :hidden="!options?.password">
                <span>当前密码</span>
                <input id="data-confirm-password" ref="password" class="profile-input" name="current_password" type="password"
                    autocomplete="current-password" maxlength="512" required :disabled="!options?.password">
            </label>
            <div class="confirm-actions">
                <button id="data-confirm-cancel" type="button" autofocus @click="finishConfirmation(null)">取消</button>
                <button id="data-confirm-submit" class="confirm-primary" :class="{'confirm-destructive': options?.destructive}"
                    type="submit" value="confirm">{{ options?.action }}</button>
            </div>
        </form>
    </dialog>
    <dialog id="alert-window-container" ref="alert" role="alertdialog" aria-labelledby="alert-window-title"
        aria-describedby="alert-window-message" @cancel.prevent="dismissAlert">
        <div class="alert-window">
            <div class="alert-window-header"><span id="alert-window-title">提示</span></div>
            <div class="alert-window-content" role="alert"><p id="alert-window-message">{{ message }}</p></div>
            <div class="alert-window-button"><button type="button" @click="dismissAlert">确定</button></div>
        </div>
    </dialog>
</template>
