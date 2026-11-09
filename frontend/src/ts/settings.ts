import {modelAPI, settingsAPI} from './api.ts';
import {modelConfig} from './global_config.ts';
import {preferences} from './preferences.ts';
import type {Preferences} from './preferences.ts';
import {openPopup, closePopup, menuSelect} from './ui.ts';

const select = (id: string) => document.getElementById(id) as HTMLSelectElement;
const input = (id: string) => document.getElementById(id) as HTMLInputElement;
const fields = () => document.getElementById('preferences-fields') as HTMLFieldSetElement;
const status = () => document.getElementById('preferences-status')!;

function updateModelDisplay() {
    document.getElementById('display-merchant')!.textContent = modelConfig.model.model_merchant || '未配置';
    document.getElementById('display-model')!.textContent = modelConfig.model.model_name || '未配置';
    document.getElementById('display-base-url')!.textContent = modelConfig.model.base_url || '厂商默认地址';
}

function updatePlaybackControl() {
    select('reply-interval').disabled = select('playback-mode').value === 'instant';
}

function renderPreferences() {
    select('playback-mode').value = preferences.playback_mode;
    const interval = select('reply-interval');
    if (!Array.from(interval.options).some(option => Number(option.value) === preferences.reply_interval_ms)) {
        interval.add(new Option(`${preferences.reply_interval_ms / 1000} 秒`, String(preferences.reply_interval_ms)));
    }
    interval.value = String(preferences.reply_interval_ms);
    select('reply-length').value = preferences.reply_length;
    input('split-reply').checked = preferences.split_reply;
    select('send-shortcut').value = preferences.send_shortcut;
    updatePlaybackControl();
}

export async function loadSettings() {
    fields().disabled = true;
    status().textContent = '正在读取设置…';
    try {
        const saved = await settingsAPI();
        Object.assign(preferences, saved.preferences);
        if (saved.model && !modelConfig.model_valid) modelConfig.model = {...saved.model};
        renderPreferences();
        updateModelDisplay();
        fields().disabled = false;
        status().textContent = '';
    } catch (error) {
        status().textContent = error instanceof Error ? error.message : '设置读取失败';
        throw error;
    }
}

export function initSettings() {
    const container = document.getElementById('setting-container')!;
    const modal = document.getElementById('model-edit-modal')!;
    const form = document.getElementById('model-config-form') as HTMLFormElement;
    const modelStatus = document.getElementById('model-test-status')!;
    const merchant = select('modelSelect');
    const name = input('modelName');
    const address = input('model-base-url');
    const suggestions: Record<string, string[]> = {
        deepseek: ['deepseek-v4-flash', 'deepseek-v4-pro'],
        tongyi: ['qwen3-max', 'qwen-plus', 'qwen-turbo', 'qwen-flash']
    };
    let modelBusy = false;
    let preferencesBusy = false;

    function updateSuggestions() {
        const list = document.getElementById('model-name-options')!;
        list.replaceChildren(...(suggestions[merchant.value] || []).map(value => new Option(value, value)));
    }

    menuSelect[1].addEventListener('click', event => {
        event.stopPropagation();
        updateModelDisplay();
        openPopup(container);
    });
    document.querySelector('.close-setting')!.addEventListener('click', async () => {
        if (await closePopup(container)) {
            modal.classList.remove('open', 'closing');
            modal.inert = false;
        }
    });
    document.getElementById('open-modify-btn')!.addEventListener('click', () => {
        if (!modelBusy) {
            merchant.value = modelConfig.model.model_merchant || 'deepseek';
            updateSuggestions();
            name.value = modelConfig.model.model_name || suggestions[merchant.value]![0]!;
            address.value = modelConfig.model.base_url || '';
            modelStatus.textContent = '';
        }
        openPopup(modal);
    });
    document.getElementById('cancel-modify-btn')!.addEventListener('click', () => { void closePopup(modal); });
    merchant.addEventListener('change', () => {
        updateSuggestions();
        name.value = suggestions[merchant.value]![0]!;
        address.value = '';
        modelStatus.textContent = '';
    });
    form.addEventListener('input', () => { modelStatus.textContent = ''; });

    async function configure(testOnly: boolean) {
        if (modelBusy || !form.reportValidity()) return;
        const model = {model_merchant: merchant.value, model_name: name.value.trim(), base_url: address.value.trim()};
        if (!model.model_name) { modelStatus.textContent = '请输入模型名'; return; }
        modelBusy = true;
        form.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLButtonElement>('input,select,button')
            .forEach(control => { control.disabled = true; });
        modelStatus.textContent = testOnly ? '正在测试连接…' : '正在验证并保存模型配置…';
        try {
            const result = await modelAPI(model, testOnly);
            modelStatus.textContent = result.info || (result.status ? '操作成功' : '连接失败');
            if (result.status && !testOnly) {
                modelConfig.model = model;
                modelConfig.model_valid = true;
                updateModelDisplay();
                await closePopup(modal);
            }
        } catch {
            modelStatus.textContent = '请求失败或超时，请检查后端、接口地址和模型配置。';
        } finally {
            modelBusy = false;
            form.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLButtonElement>('input,select,button')
                .forEach(control => { control.disabled = false; });
        }
    }

    form.addEventListener('submit', event => { event.preventDefault(); void configure(false); });
    document.getElementById('test-model-btn')!.addEventListener('click', () => { void configure(true); });
    select('playback-mode').addEventListener('change', updatePlaybackControl);
    const reload = document.getElementById('reload-settings') as HTMLButtonElement;
    reload.addEventListener('click', async () => {
        if (preferencesBusy) return;
        reload.disabled = true;
        try { await loadSettings(); } catch { /* 表单状态已展示错误。 */ }
        finally { reload.disabled = false; }
    });
    document.getElementById('preferences-form')!.addEventListener('submit', async event => {
        event.preventDefault();
        if (preferencesBusy || fields().disabled) return;
        const next: Preferences = {
            playback_mode: select('playback-mode').value as Preferences['playback_mode'],
            reply_interval_ms: Number(select('reply-interval').value),
            reply_length: select('reply-length').value as Preferences['reply_length'],
            split_reply: input('split-reply').checked,
            send_shortcut: select('send-shortcut').value as Preferences['send_shortcut']
        };
        preferencesBusy = true;
        fields().disabled = true;
        reload.disabled = true;
        status().textContent = '正在保存…';
        try {
            const saved = await settingsAPI(next);
            Object.assign(preferences, saved.preferences);
            renderPreferences();
            status().textContent = '聊天设置已保存';
        } catch (error) {
            status().textContent = error instanceof Error ? error.message : '保存失败，请重试';
        } finally {
            preferencesBusy = false;
            fields().disabled = false;
            reload.disabled = false;
        }
    });
}
