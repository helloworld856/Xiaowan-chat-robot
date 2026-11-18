import {canManageModel, getRequestGeneration, modelAPI, settingsAPI} from './api.ts';
import type {ModelOptions, SavedSettings} from './api.ts';
import {modelConfig} from './global_config.ts';
import {preferences} from './preferences.ts';
import type {Preferences} from './preferences.ts';
import {openPopup, closePopup, resetPopup, closeMenu, menuBtn, menuSelect} from './ui.ts';
import {switchTheme} from './utils.ts';

const select = (id: string) => document.getElementById(id) as HTMLSelectElement;
const input = (id: string) => document.getElementById(id) as HTMLInputElement;
const fields = () => document.getElementById('preferences-fields') as HTMLFieldSetElement;
const status = () => document.getElementById('preferences-status')!;
let modelRevision = 0;
let modelOptions: ModelOptions | undefined;
const settingsPopup = () => document.getElementById('setting-container') as HTMLDialogElement;
const modelPopup = () => document.getElementById('model-edit-modal') as HTMLDialogElement;

function updateModelDisplay() {
    document.getElementById('display-merchant')!.textContent = modelConfig.model.model_merchant || '未配置';
    document.getElementById('display-model')!.textContent = modelConfig.model.model_name || '未配置';
    const provider = modelOptions?.providers[modelConfig.model.model_merchant];
    document.getElementById('display-base-url')!.textContent = modelConfig.model.base_url || provider?.default_address || '未配置';
    document.getElementById('display-model-status')!.textContent = modelConfig.model_valid ? '可用' : '未就绪';
}

function renderPreferences() {
    select('send-shortcut').value = preferences.send_shortcut;
    select('theme-select').value = preferences.theme;
    switchTheme(preferences.theme);
}

function applySettings(saved: SavedSettings, requestedModelRevision: number) {
    Object.assign(preferences, saved.preferences);
    // 迟到的读取可更新偏好，但不能覆盖用户刚切换的模型。
    if (requestedModelRevision === modelRevision) {
        modelOptions = saved.model_options;
        modelConfig.model = saved.model ? {...saved.model} : {model_merchant: '', model_name: '', base_url: ''};
        modelConfig.model_valid = saved.model_ready;
        updateModelDisplay();
    }
}

function updateApiKeyPrompt(apiKey: HTMLInputElement, hasSavedKey: boolean) {
    apiKey.required = !hasSavedKey;
    // 星号只作占位提示，提交时仍用空值表示沿用密钥。
    apiKey.placeholder = hasSavedKey ? '********' : '';
}

export function updateSettingsPermissions() {
    const admin = canManageModel();
    const apiKey = input('model-api-key');
    apiKey.disabled = false;
    apiKey.value = '';
    updateApiKeyPrompt(apiKey, false);
    document.getElementById('model-address-help')!.textContent = admin
        ? '自定义地址须经服务器批准，并兼容 OpenAI 接口。更换地址时请重新填写 API Key。'
        : '自定义地址须使用公网 HTTPS，并兼容 OpenAI 接口。';
    document.getElementById('open-modify-btn')!.textContent = admin ? '修改模型配置' : '配置个人模型';
    document.getElementById('submit-model-btn')!.textContent = admin ? '保存并切换' : '保存模型';
}

export function resetSettings() {
    resetPopup(modelPopup());
    resetPopup(settingsPopup());
    preferences.send_shortcut = 'enter';
    preferences.theme = 'theme0';
    modelOptions = undefined;
    modelConfig.model = {model_merchant: '', model_name: '', base_url: ''};
    modelConfig.model_valid = false;
    renderPreferences();
    updateModelDisplay();
    fields().disabled = true;
    status().textContent = '';
    (document.getElementById('reload-settings') as HTMLButtonElement).disabled = false;
    const form = document.getElementById('model-config-form') as HTMLFormElement;
    form.reset();
    form.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLButtonElement>('input,select,button')
        .forEach(control => { control.disabled = false; });
    updateSettingsPermissions();
    document.getElementById('model-test-status')!.textContent = '';
}

export async function loadSettings() {
    const generation = getRequestGeneration();
    const requestedModelRevision = modelRevision;
    fields().disabled = true;
    status().textContent = '正在读取设置…';
    try {
        const saved = await settingsAPI();
        if (generation !== getRequestGeneration()) return;
        applySettings(saved, requestedModelRevision);
        renderPreferences();
        fields().disabled = false;
        status().textContent = '';
    } catch (error) {
        if (generation === getRequestGeneration()) status().textContent = error instanceof Error ? error.message : '设置读取失败';
        throw error;
    }
}

export function initSettings() {
    const container = settingsPopup();
    const modal = modelPopup();
    const form = document.getElementById('model-config-form') as HTMLFormElement;
    const modelStatus = document.getElementById('model-test-status')!;
    const merchant = select('modelSelect');
    const name = select('modelName');
    const address = input('model-base-url');
    const apiKey = input('model-api-key');
    let loadedAddress = '';
    let apiKeyEdited = false;
    let modelOperation: number | null = null;
    let preferencesSave: {generation: number; pending?: Preferences} | undefined;

    function resetApiKey() {
        apiKey.value = '';
        apiKeyEdited = false;
    }

    function loadProvider(preferredModel?: string) {
        const provider = modelOptions?.providers[merchant.value];
        const names = provider?.names || [];
        const preferred = preferredModel || provider?.saved_model || '';
        name.replaceChildren(...names.map(value => new Option(value, value)));
        name.value = names.includes(preferred) ? preferred : provider?.default_model || '';
        loadedAddress = provider?.saved_address || provider?.default_address || '';
        address.value = loadedAddress;
        resetApiKey();
        updateApiKeyPrompt(apiKey, provider?.has_saved_key === true);
    }

    menuSelect[1].addEventListener('click', event => {
        event.stopPropagation();
        closeMenu();
        menuBtn.focus();
        updateModelDisplay();
        openPopup(container);
    });
    document.querySelector('.close-setting')!.addEventListener('click', () => { void closePopup(container); });
    container.addEventListener('cancel', event => {
        event.preventDefault();
        void closePopup(container);
    });
    document.getElementById('open-modify-btn')!.addEventListener('click', () => {
        if (modelOperation !== getRequestGeneration()) {
            merchant.value = modelConfig.model.model_merchant || modelOptions?.default_provider || merchant.options?.[0]?.value || '';
            loadProvider(modelConfig.model.model_name);
            modelStatus.textContent = '';
        }
        openPopup(modal);
    });
    function cancelModelEdit() {
        if (modelOperation === getRequestGeneration()) return;
        resetApiKey();
        void closePopup(modal);
    }
    document.getElementById('cancel-modify-btn')!.addEventListener('click', cancelModelEdit);
    modal.addEventListener('cancel', event => {
        event.preventDefault();
        cancelModelEdit();
    });
    merchant.addEventListener('change', () => {
        loadProvider();
        modelStatus.textContent = '';
    });
    apiKey.addEventListener('input', event => {
        const {isTrusted, inputType} = event as InputEvent;
        apiKeyEdited = isTrusted && Boolean(inputType) && inputType !== 'insertReplacementText';
        if (!apiKeyEdited) apiKey.value = '';
    });
    form.addEventListener('input', () => { modelStatus.textContent = ''; });

    async function configure(testOnly: boolean) {
        const generation = getRequestGeneration();
        if (modelOperation === generation) return;
        const admin = canManageModel();
        // 自动填充的登录密码不能用于测试或替换已保存的 API Key。
        if (!apiKeyEdited || apiKey.matches?.(':is(:autofill, :-webkit-autofill)')) apiKey.value = '';
        const key = apiKey.value.trim() || undefined;
        if (!key && apiKey.required) {
            apiKey.focus();
            modelStatus.textContent = '请填写此厂商的 API Key';
            return;
        }
        if (!form.reportValidity()) return;
        const provider = modelOptions?.providers[merchant.value];
        const enteredAddress = address.value.trim();
        // 地址未改动时沿用服务端记录，清空或选回默认地址时恢复厂商协议。
        const base_url = enteredAddress === loadedAddress ? undefined
            : enteredAddress === provider?.default_address ? '' : enteredAddress;
        const model = {model_merchant: merchant.value, model_name: name.value.trim(), base_url};
        if (!model.model_name) { modelStatus.textContent = '请选择模型'; return; }
        modelOperation = generation;
        form.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLButtonElement>('input,select,button')
            .forEach(control => { control.disabled = true; });
        modelStatus.textContent = testOnly ? '正在测试连接…' : '正在验证并保存模型配置…';
        try {
            const result = await modelAPI(model, testOnly, key, !admin);
            if (generation !== getRequestGeneration()) return;
            modelStatus.textContent = result.info || (result.status ? '操作成功' : '连接失败');
            // 探测通过只显示提示，保存成功才更新当前启用的模型。
            if (result.status && !testOnly) {
                modelRevision++;
                const currentProvider = modelOptions?.providers[model.model_merchant];
                const effectiveAddress = enteredAddress || currentProvider?.default_address || '';
                if (currentProvider) {
                    currentProvider.saved_model = model.model_name;
                    currentProvider.has_saved_key = true;
                    currentProvider.saved_address = effectiveAddress;
                }
                modelConfig.model = {...model, base_url: effectiveAddress};
                modelConfig.model_valid = true;
                resetApiKey();
                updateModelDisplay();
                await closePopup(modal);
            }
        } catch {
            if (generation === getRequestGeneration()) {
                modelStatus.textContent = '请求失败或超时，请检查后端、接口地址和模型配置。';
            }
        } finally {
            if (modelOperation === generation) modelOperation = null;
            if (generation === getRequestGeneration()) {
                form.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLButtonElement>('input,select,button')
                    .forEach(control => { control.disabled = false; });
            }
        }
    }

    form.addEventListener('submit', event => { event.preventDefault(); void configure(false); });
    document.getElementById('test-model-btn')!.addEventListener('click', () => { void configure(true); });
    const reload = document.getElementById('reload-settings') as HTMLButtonElement;
    reload.addEventListener('click', async () => {
        const generation = getRequestGeneration();
        if (preferencesSave?.generation === generation) return;
        reload.disabled = true;
        try { await loadSettings(); } catch { /* 表单状态已展示错误。 */ }
        finally { if (generation === getRequestGeneration()) reload.disabled = false; }
    });

    async function savePreferences() {
        if (fields().disabled) return;
        const generation = getRequestGeneration();
        const next: Preferences = {
            send_shortcut: select('send-shortcut').value as Preferences['send_shortcut'],
            theme: select('theme-select').value as Preferences['theme']
        };
        switchTheme(next.theme);
        if (preferencesSave?.generation === generation) {
            // 每次提交整组偏好，当前请求结束后只补交最新一组选择。
            preferencesSave.pending = next;
            return;
        }

        const operation: NonNullable<typeof preferencesSave> = {generation, pending: next};
        preferencesSave = operation;
        reload.disabled = true;
        status().textContent = '正在保存…';
        try {
            while (operation.pending) {
                const selection = operation.pending;
                operation.pending = undefined;
                const requestedModelRevision = modelRevision;
                try {
                    const saved = await settingsAPI(selection);
                    if (generation !== getRequestGeneration()) return;
                    applySettings(saved, requestedModelRevision);
                    if (!operation.pending) status().textContent = '偏好已自动保存';
                } catch (error) {
                    if (generation !== getRequestGeneration()) return;
                    if (!operation.pending) status().textContent = error instanceof Error ? error.message : '保存失败，请重试';
                }
                // 较早的保存结果更新已确认的偏好，等最新选择也提交后才同步界面。
                if (!operation.pending) renderPreferences();
            }
        } finally {
            if (preferencesSave === operation) preferencesSave = undefined;
            if (generation === getRequestGeneration()) reload.disabled = false;
        }
    }

    select('send-shortcut').addEventListener('change', () => { void savePreferences(); });
    select('theme-select').addEventListener('change', () => { void savePreferences(); });
}
