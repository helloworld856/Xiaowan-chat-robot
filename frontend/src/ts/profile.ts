import {AuthRequiredError, CancelledRequestError, changePasswordAPI, profileAPI} from './api.ts';
import type {AvatarId, ProfileInfo} from './api.ts';
import {menuBtn, menuSelect} from './ui.ts';

let current: ProfileInfo | null = null;
const element = (id: string) => document.getElementById(id)!;
const input = (id: string) => element(id) as HTMLInputElement;
const dialog = () => element('profile-dialog') as HTMLDialogElement;

export function applyProfile(profile: ProfileInfo) {
    current = profile;
    window.USER_AVATAR = profile.avatar_url;
    window.USER_NAME = profile.nickname;
    element('current-username').textContent = profile.nickname;
    document.querySelectorAll<HTMLImageElement>('.user_avatar img').forEach(image => { image.src = profile.avatar_url; });
    document.querySelectorAll<HTMLElement>('.user_avatar').forEach(avatar => { avatar.title = profile.nickname; });
}

export async function loadProfile() {
    const profile = await profileAPI();
    applyProfile(profile);
}

export function resetProfile() {
    current = null;
    window.USER_AVATAR = '';
    window.USER_NAME = '';
    if (dialog().open) dialog().close();
    (element('profile-password-form') as HTMLFormElement).reset();
}

export function initProfile() {
    const popup = dialog();
    const form = element('profile-form') as HTMLFormElement;
    const fields = element('profile-fields') as HTMLFieldSetElement;
    const passwordForm = element('profile-password-form') as HTMLFormElement;
    const passwordFields = element('profile-password-fields') as HTMLFieldSetElement;
    const nickname = input('profile-nickname');
    const preview = element('profile-preview') as HTMLImageElement;
    const avatars = element('profile-avatars');
    const close = element('profile-close') as HTMLButtonElement;
    const cancel = element('profile-cancel') as HTMLButtonElement;
    const save = element('profile-save') as HTMLButtonElement;
    const back = element('profile-password-back') as HTMLButtonElement;
    const passwordSave = element('profile-password-save') as HTMLButtonElement;
    let selected: AvatarId = 'cat';
    let busy = false;
    let ready = false;

    function message(id: string, text = '', error = false) {
        element(id).textContent = text;
        element(id).classList.toggle('is-error', error);
    }
    function setBusy(value: boolean) {
        busy = value;
        fields.disabled = value;
        passwordFields.disabled = value;
        for (const button of [close, cancel, save, back, passwordSave]) button.disabled = value;
    }
    function showPassword(show: boolean) {
        form.hidden = show;
        passwordForm.hidden = !show;
        element('profile-title').textContent = show ? '修改密码' : '个人资料';
        passwordForm.reset();
        message('profile-password-status');
        if (show) input('profile-current-password').focus();
    }
    function selectAvatar(id: AvatarId) {
        selected = id;
        const avatar = current?.avatars.find(avatar => avatar.id === id);
        if (avatar) preview.src = avatar.url;
        avatars.querySelectorAll<HTMLInputElement>('input').forEach(radio => { radio.checked = radio.value === id; });
    }
    function fillProfile() {
        if (!current) return;
        nickname.value = current.nickname;
        element('profile-display-name').textContent = current.nickname;
        element('profile-username').textContent = current.username;
        avatars.replaceChildren(...current.avatars.map(avatar => {
            const label = document.createElement('label');
            label.className = 'avatar-choice';
            label.title = avatar.name + (avatar.id === current!.default_avatar_id ? '（默认）' : '');
            const radio = document.createElement('input');
            radio.type = 'radio';
            radio.name = 'avatar_id';
            radio.value = avatar.id;
            radio.required = true;
            radio.addEventListener('change', () => selectAvatar(avatar.id));
            const image = document.createElement('img');
            image.src = avatar.url;
            image.alt = '';
            image.width = image.height = 40;
            const name = document.createElement('span');
            name.textContent = avatar.name;
            label.append(radio, image, name);
            return label;
        }));
        selectAvatar(current.avatar_id);
    }

    menuSelect[0].addEventListener('click', async () => {
        if (popup.open) return;
        menuBtn.classList.remove('active');
        showPassword(false);
        ready = false;
        message('profile-status', '正在读取资料…');
        setBusy(true);
        popup.showModal();
        try {
            await loadProfile();
            fillProfile();
            ready = true;
            message('profile-status');
        } catch (error) {
            if (!(error instanceof AuthRequiredError || error instanceof CancelledRequestError)) {
                message('profile-status', error instanceof Error ? error.message : '读取失败，请关闭后重试', true);
            }
        } finally {
            setBusy(false);
            if (!ready) { fields.disabled = true; save.disabled = true; }
        }
    });
    for (const button of [close, cancel]) button.addEventListener('click', () => { if (!busy) popup.close(); });
    popup.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    popup.addEventListener('close', () => { passwordForm.reset(); menuSelect[0].focus(); });
    element('profile-default-avatar').addEventListener('click', () => { if (current) selectAvatar(current.default_avatar_id); });
    nickname.addEventListener('input', () => { element('profile-display-name').textContent = nickname.value.trim() || current?.username || ''; });
    element('profile-open-password').addEventListener('click', () => showPassword(true));
    back.addEventListener('click', () => showPassword(false));

    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (busy || !ready || !current || !form.reportValidity()) return;
        const value = nickname.value.trim();
        if (!value) { message('profile-status', '请输入昵称', true); return; }
        setBusy(true);
        message('profile-status', '正在保存…');
        try {
            applyProfile(await profileAPI({nickname: value, avatar_id: selected}));
            popup.close();
        } catch (error) {
            if (!(error instanceof AuthRequiredError || error instanceof CancelledRequestError)) {
                message('profile-status', error instanceof Error ? error.message : '保存失败，请重试', true);
            }
        } finally { setBusy(false); }
    });

    passwordForm.addEventListener('submit', async event => {
        event.preventDefault();
        if (busy || !passwordForm.reportValidity()) return;
        const passwords = {
            current_password: input('profile-current-password').value,
            new_password: input('profile-new-password').value,
            confirm_password: input('profile-confirm-password').value
        };
        if (passwords.new_password !== passwords.confirm_password) {
            message('profile-password-status', '两次新密码不一致', true); return;
        }
        setBusy(true);
        window.dispatchEvent(new Event('password-change-start'));
        message('profile-password-status', '正在修改…');
        try {
            await changePasswordAPI(passwords);
            popup.close();
            window.dispatchEvent(new Event('password-changed'));
        } catch (error) {
            if (!(error instanceof AuthRequiredError || error instanceof CancelledRequestError)) {
                message('profile-password-status', error instanceof Error ? error.message : '修改失败，请重试', true);
            }
        } finally {
            input('profile-current-password').value = '';
            input('profile-new-password').value = '';
            input('profile-confirm-password').value = '';
            setBusy(false);
            window.dispatchEvent(new Event('password-change-end'));
        }
    });
}
