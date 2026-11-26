<script setup lang="ts">
import {computed, nextTick, onBeforeUnmount, ref, watch} from 'vue';
import {clearHistoryAPI, getRequestGeneration, modelAPI, portraitAPI, settingsAPI} from '../ts/api.ts';
import type {ModelOptions, SavedSettings, UserPortrait} from '../ts/api.ts';
import {closeDialog, openDialog, resetDialog} from '../ts/dialogs.ts';
import type {ConfirmOptions} from '../ts/dialogs.ts';
import type {ModelInfo} from '../ts/global_config.ts';
import type {Preferences} from '../ts/preferences.ts';

const props = defineProps<{
    isAdmin: boolean;
    preferences: Preferences;
    chatBusy: boolean;
    showAlert: (message: string) => void;
    confirmAction: (options: ConfirmOptions) => Promise<string | null>;
}>();
const emit = defineEmits<{
    'preferences-change': [preferences: Preferences];
    'history-cleared': [];
    'clearing-history': [busy: boolean];
}>();

const settingsDialog = ref<HTMLDialogElement | null>(null);
const modelDialog = ref<HTMLDialogElement | null>(null);
const portraitDialog = ref<HTMLDialogElement | null>(null);
const modelForm = ref<HTMLFormElement | null>(null);
const addressInput = ref<HTMLInputElement | null>(null);
const keyInput = ref<HTMLInputElement | null>(null);
const autofillSelector = ':is(:autofill, :-webkit-autofill)';

const preferenceDraft = ref<Preferences>({...props.preferences});
const preferencesDisabled = ref(true);
const modelOptions = ref<ModelOptions>();
const activeModel = ref<ModelInfo | null>(null);
const modelReady = ref(false);
const merchant = ref('deepseek');
const modelName = ref('');
const address = ref('');
const keyVisible = ref(false);
const modelOperation = ref<'test' | 'save' | null>(null);
const modelBusy = computed(() => modelOperation.value !== null);
const historyOperation = ref<'idle' | 'confirming' | 'clearing'>('idle');

function emptyPortrait(): UserPortrait {
    return {
        manual: {call_me: '', city: '', birthday: '', occupation: '', chat_preference: ''},
        learned: [],
        auto_learn: true,
    };
}
const portraitDraft = ref<UserPortrait>(emptyPortrait());
const portraitNewNote = ref('');
const portraitOperation = ref<'idle' | 'loading' | 'saving'>('idle');
const portraitBusy = computed(() => portraitOperation.value !== 'idle');

let resetRevision = 0;
let modelRevision = 0;
let settingsLoadFailed = false;
let loadedAddress = '';
let typedApiKey = '';

const modelAction = computed(() => props.isAdmin ? '修改模型配置' : '配置个人模型');
const provider = computed(() => modelOptions.value?.providers[merchant.value]);
const hasSavedKey = computed(() => provider.value?.has_saved_key === true);
const activeAddress = computed(() => {
    const model = activeModel.value;
    return model?.base_url || (model && modelOptions.value?.providers[model.model_merchant]?.default_address) || '未配置';
});

function captureSession() {
    const generation = getRequestGeneration();
    const revision = resetRevision;
    return () => generation === getRequestGeneration() && revision === resetRevision;
}

function resetApiKey() {
    if (keyInput.value) keyInput.value.value = '';
    typedApiKey = '';
    keyVisible.value = false;
}

function discardAutofilledApiKey() {
    const input = keyInput.value;
    if (input && (input.value !== typedApiKey || input.matches(autofillSelector))) resetApiKey();
}

function trackApiKey(event: Event) {
    const {isTrusted, inputType} = event as InputEvent;
    const input = keyInput.value;
    // 浏览器填充的登录密码不能显示，也不能提交给模型接口。
    if (input && isTrusted && inputType && inputType !== 'insertReplacementText' && !input.matches(autofillSelector)) {
        typedApiKey = input.value;
    } else {
        resetApiKey();
    }
}

function toggleApiKey() {
    discardAutofilledApiKey();
    keyVisible.value = !keyVisible.value && Boolean(keyInput.value?.value);
}

function setProvider(preferredModel = '') {
    const selected = provider.value;
    const preferred = preferredModel || selected?.saved_model;
    modelName.value = preferred && selected?.names.includes(preferred) ? preferred : selected?.default_model || '';
    loadedAddress = selected?.saved_address || selected?.default_address || '';
    address.value = loadedAddress;
    resetApiKey();
}

function showAddressHelp() {
    const rule = props.isAdmin
        ? '自定义地址须经服务器批准，并兼容 OpenAI 接口。'
        : '自定义地址须使用公网 HTTPS，并兼容 OpenAI 接口。';
    props.showAlert(`${rule}\n清空接口地址可恢复厂商默认地址。更换或恢复地址时，请重新填写 API Key。`);
}

function applySettings(saved: SavedSettings, requestedModelRevision: number) {
    preferenceDraft.value = {...saved.preferences};
    emit('preferences-change', {...saved.preferences});
    // 迟到的读取可更新偏好，但不能覆盖用户刚切换的模型。
    if (requestedModelRevision === modelRevision) {
        modelOptions.value = saved.model_options;
        activeModel.value = saved.model;
        modelReady.value = saved.model_ready;
    }
}

async function open() {
    preferenceDraft.value = {...props.preferences};
    if (settingsDialog.value) openDialog(settingsDialog.value);
    if (!settingsLoadFailed) return;
    const isCurrent = captureSession();
    try {
        await load();
    } catch (error) {
        if (isCurrent()) props.showAlert(error instanceof Error ? error.message : '读取设置失败，请关闭后重试。');
    }
}

function closeSettings() {
    if (settingsDialog.value) return closeDialog(settingsDialog.value);
}

async function openModel() {
    const catalog = modelOptions.value;
    if (!catalog || modelBusy.value) return;
    const isCurrent = captureSession();
    const model = activeModel.value;
    const activeProvider = model && catalog.providers[model.model_merchant];
    merchant.value = activeProvider ? model.model_merchant : catalog.default_provider;
    setProvider(activeProvider ? model.model_name : undefined);
    await nextTick();
    if (isCurrent() && modelDialog.value) openDialog(modelDialog.value);
}

function closeModel() {
    if (modelBusy.value || !modelDialog.value) return;
    resetApiKey();
    return closeDialog(modelDialog.value);
}

async function openPortrait() {
    if (portraitBusy.value || !portraitDialog.value) return;
    const isCurrent = captureSession();
    portraitNewNote.value = '';
    portraitOperation.value = 'loading';
    openDialog(portraitDialog.value);
    try {
        const loaded = await portraitAPI();
        if (!isCurrent()) return;
        portraitDraft.value = {
            manual: {...loaded.manual},
            learned: [...loaded.learned],
            auto_learn: loaded.auto_learn,
        };
    } catch (error) {
        if (isCurrent()) {
            props.showAlert(error instanceof Error ? error.message : '读取用户画像失败，请稍后重试');
            if (portraitDialog.value) void closeDialog(portraitDialog.value);
        }
    } finally {
        if (isCurrent()) portraitOperation.value = 'idle';
    }
}

function closePortrait() {
    if (portraitBusy.value || !portraitDialog.value) return;
    portraitNewNote.value = '';
    return closeDialog(portraitDialog.value);
}

function addPortraitNote() {
    const text = portraitNewNote.value.replace(/\s+/g, ' ').trim();
    if (!text) return;
    if (portraitDraft.value.learned.length >= 30) {
        props.showAlert('最多保存 30 条印象，请先删掉不需要的条目。');
        return;
    }
    if (!portraitDraft.value.learned.includes(text)) {
        portraitDraft.value.learned.push(text.slice(0, 120));
    }
    portraitNewNote.value = '';
}

function removePortraitNote(index: number) {
    portraitDraft.value.learned.splice(index, 1);
}

function clearPortraitNotes() {
    portraitDraft.value.learned = [];
}

async function savePortrait() {
    if (portraitBusy.value) return;
    addPortraitNote();
    const isCurrent = captureSession();
    portraitOperation.value = 'saving';
    let message = '用户画像已保存。';
    let ok = false;
    try {
        const payload: UserPortrait = {
            manual: {
                call_me: portraitDraft.value.manual.call_me.trim(),
                city: portraitDraft.value.manual.city.trim(),
                birthday: portraitDraft.value.manual.birthday.trim(),
                occupation: portraitDraft.value.manual.occupation.trim(),
                chat_preference: portraitDraft.value.manual.chat_preference.trim(),
            },
            learned: [...portraitDraft.value.learned],
            auto_learn: portraitDraft.value.auto_learn,
        };
        const saved = await portraitAPI(payload);
        if (!isCurrent()) return;
        portraitDraft.value = {
            manual: {...saved.manual},
            learned: [...saved.learned],
            auto_learn: saved.auto_learn,
        };
        ok = true;
    } catch (error) {
        if (!isCurrent()) return;
        message = error instanceof Error ? error.message : '保存用户画像失败，请稍后重试';
    } finally {
        if (isCurrent()) portraitOperation.value = 'idle';
    }
    if (ok && portraitDialog.value && isCurrent()) await closeDialog(portraitDialog.value);
    if (isCurrent()) props.showAlert(message);
}

async function load() {
    const isCurrent = captureSession();
    const requestedModelRevision = modelRevision;
    preferencesDisabled.value = true;
    settingsLoadFailed = false;
    try {
        const saved = await settingsAPI();
        if (!isCurrent()) return;
        applySettings(saved, requestedModelRevision);
        preferencesDisabled.value = false;
    } catch (error) {
        if (isCurrent()) settingsLoadFailed = true;
        throw error;
    }
}

async function savePreferences() {
    if (preferencesDisabled.value) return;
    const isCurrent = captureSession();
    const requestedModelRevision = modelRevision;
    preferencesDisabled.value = true;
    let message = '偏好保存成功。';
    try {
        const saved = await settingsAPI({...preferenceDraft.value});
        if (!isCurrent()) return;
        applySettings(saved, requestedModelRevision);
    } catch {
        message = '偏好保存失败，请重试。';
    } finally {
        if (isCurrent()) preferencesDisabled.value = false;
    }
    if (isCurrent()) props.showAlert(message);
}

async function configureModel(testOnly: boolean) {
    if (modelBusy.value || !modelForm.value) return;
    discardAutofilledApiKey();
    const key = keyInput.value?.value.trim() || undefined;
    keyVisible.value = false;
    if (!key && !hasSavedKey.value) {
        keyInput.value?.focus();
        props.showAlert('请填写此厂商的 API Key');
        return;
    }
    if (!modelName.value.trim()) {
        props.showAlert('请选择模型');
        return;
    }
    if (addressInput.value?.validity?.typeMismatch) {
        addressInput.value.focus();
        props.showAlert('请输入有效的接口地址。');
        return;
    }
    const isCurrent = captureSession();
    const selected = provider.value;
    const enteredAddress = address.value.trim();
    // 地址未改动时沿用服务端记录，清空或选回默认地址时恢复厂商协议。
    const base_url = enteredAddress === loadedAddress ? undefined
        : enteredAddress === selected?.default_address ? '' : enteredAddress;
    const model = {model_merchant: merchant.value, model_name: modelName.value.trim(), base_url};
    modelOperation.value = testOnly ? 'test' : 'save';
    let feedback: string;
    try {
        const result = await modelAPI(model, testOnly, key, !props.isAdmin);
        if (!isCurrent()) return;
        feedback = result.info || (result.status ? '操作成功' : '连接失败');
        if (result.status && !testOnly) {
            modelRevision++;
            const savedProvider = modelOptions.value?.providers[model.model_merchant];
            const effectiveAddress = enteredAddress || savedProvider?.default_address || '';
            if (savedProvider) {
                savedProvider.saved_model = model.model_name;
                savedProvider.has_saved_key = true;
                savedProvider.saved_address = effectiveAddress;
            }
            activeModel.value = {...model, base_url: effectiveAddress};
            modelReady.value = true;
            resetApiKey();
            if (modelDialog.value) await closeDialog(modelDialog.value);
        }
    } catch {
        feedback = '请求失败或超时，请检查后端、接口地址和模型配置。';
    } finally {
        if (isCurrent()) modelOperation.value = null;
    }
    if (isCurrent()) props.showAlert(feedback);
}

async function clearHistory() {
    if (historyOperation.value !== 'idle') return;
    const busyMessage = '请等当前回复完成后再清空聊天记录。';
    if (props.chatBusy) {
        props.showAlert(busyMessage);
        return;
    }
    const isCurrent = captureSession();
    historyOperation.value = 'confirming';
    try {
        const confirmed = await props.confirmAction({
            title: '确认清空聊天记录？',
            description: '此账户的所有聊天记录和聊天摘要将永久删除，且无法恢复。',
            action: '清空聊天记录',
            destructive: true
        });
        if (confirmed === null || !isCurrent()) return;
        if (props.chatBusy) {
            props.showAlert(busyMessage);
            return;
        }

        historyOperation.value = 'clearing';
        emit('clearing-history', true);
        const result = await clearHistoryAPI();
        if (!isCurrent()) return;
        if (!result.ok) throw new Error('清空聊天记录失败，请稍后重试');
        emit('history-cleared');
        props.showAlert('聊天记录已清空。');
    } catch (error) {
        if (isCurrent()) {
            props.showAlert(error instanceof Error ? error.message : '清空聊天记录失败，请稍后重试');
        }
    } finally {
        if (isCurrent()) {
            if (historyOperation.value === 'clearing') emit('clearing-history', false);
            historyOperation.value = 'idle';
        }
    }
}

function reset() {
    resetRevision++;
    settingsLoadFailed = false;
    if (modelDialog.value) resetDialog(modelDialog.value);
    if (portraitDialog.value) resetDialog(portraitDialog.value);
    if (settingsDialog.value) resetDialog(settingsDialog.value);
    portraitDraft.value = emptyPortrait();
    portraitNewNote.value = '';
    portraitOperation.value = 'idle';
    activeModel.value = null;
    modelOptions.value = undefined;
    modelReady.value = false;
    modelOperation.value = null;
    merchant.value = 'deepseek';
    setProvider();
    preferenceDraft.value = {send_shortcut: 'enter', theme: 'theme0', chat_font_size: 'standard'};
    preferencesDisabled.value = true;
    if (historyOperation.value === 'clearing') emit('clearing-history', false);
    historyOperation.value = 'idle';
}

watch(() => props.isAdmin, resetApiKey);
onBeforeUnmount(reset);
defineExpose({open, load, reset});
</script>

<template>
    <dialog id="setting-container" ref="settingsDialog" aria-labelledby="settings-title" @cancel.prevent="closeSettings">
        <div class="setting">
            <div class="setting-head">
                <span id="settings-title">设置</span>
                <button type="button" class="settings-button close-setting" aria-label="关闭设置" autofocus @click="closeSettings">
                    <svg viewBox="0 0 17 17" height="17" width="17" aria-hidden="true">
                        <line x1="0" y1="0" x2="17" y2="17" stroke="currentColor" stroke-width="2"/>
                        <line x1="17" y1="0" x2="0" y2="17" stroke="currentColor" stroke-width="2"/>
                    </svg>
                </button>
            </div>
            <div class="setting-content">
                <div id="model-info-view">
                    <h2>模型连接</h2>
                    <div class="info-item">
                        <label>接口地址：</label>
                        <span id="display-base-url">{{ activeAddress }}</span>
                    </div>
                    <div class="info-item">
                        <label>模型厂商：</label>
                        <span id="display-merchant">{{ activeModel?.model_merchant || '未配置' }}</span>
                    </div>
                    <div class="info-item">
                        <label>模型名称：</label>
                        <span id="display-model">{{ activeModel?.model_name || '未配置' }}</span>
                    </div>
                    <div class="info-item">
                        <label>模型状态：</label>
                        <span id="display-model-status">{{ modelReady ? '可用' : '未就绪' }}</span>
                    </div>
                    <button type="button" id="open-modify-btn" class="settings-button settings-section-action"
                        :disabled="!modelOptions" @click="openModel">
                        {{ modelAction }}
                    </button>
                </div>

                <section id="preferences-panel">
                    <form id="preferences-form" @submit.prevent="savePreferences">
                        <fieldset id="preferences-fields" :disabled="preferencesDisabled">
                            <legend>偏好</legend>
                            <label class="preference-row" for="theme-select">
                                <span>主题</span>
                                <select id="theme-select" v-model="preferenceDraft.theme" class="settings-select">
                                    <option value="theme0">默认白</option>
                                    <option value="theme1">海阔天空</option>
                                    <option value="theme2">夏日海岸</option>
                                    <option value="theme3">樱雨绵绵</option>
                                    <option value="theme4">晚风夜色</option>
                                </select>
                            </label>
                            <label class="preference-row" for="chat-font-size">
                                <span>聊天字号</span>
                                <select id="chat-font-size" v-model="preferenceDraft.chat_font_size" class="settings-select">
                                    <option value="small">小</option>
                                    <option value="standard">标准</option>
                                    <option value="large">大</option>
                                </select>
                            </label>
                            <label class="preference-row" for="send-shortcut">
                                <span>发送快捷键</span>
                                <select id="send-shortcut" v-model="preferenceDraft.send_shortcut" class="settings-select">
                                    <option value="enter">Enter 发送</option>
                                    <option value="ctrl_enter">Ctrl+Enter 发送</option>
                                </select>
                            </label>
                            <button type="submit" id="save-preferences" class="settings-button settings-section-action">保存偏好</button>
                        </fieldset>
                    </form>
                </section>

                <section id="chat-settings-panel" aria-labelledby="chat-settings-title">
                    <h2 id="chat-settings-title">聊天设置</h2>
                    <div class="chat-settings-actions">
                        <button id="open-portrait-btn" class="settings-button settings-section-action"
                            type="button" @click="openPortrait">用户画像</button>
                        <button id="clear-chat-history" class="settings-button settings-button-danger settings-section-action"
                            type="button" :disabled="historyOperation === 'clearing'" @click="clearHistory">
                            {{ historyOperation === 'clearing' ? '正在清空…' : '清空聊天记录' }}
                        </button>
                    </div>
                </section>

                <dialog id="portrait-edit-modal" ref="portraitDialog" class="modal" aria-labelledby="portrait-modal-title"
                    @cancel.prevent="closePortrait">
                    <div class="modal-content">
                        <div class="portrait-head">
                            <h3 id="portrait-modal-title">用户画像</h3>
                            <button type="button" class="settings-button close-setting" aria-label="关闭用户画像"
                                :disabled="portraitBusy" @click="closePortrait">
                                <svg viewBox="0 0 17 17" height="17" width="17" aria-hidden="true">
                                    <line x1="0" y1="0" x2="17" y2="17" stroke="currentColor" stroke-width="2"/>
                                    <line x1="17" y1="0" x2="0" y2="17" stroke="currentColor" stroke-width="2"/>
                                </svg>
                            </button>
                        </div>
                        <form id="portrait-form" novalidate :aria-busy="portraitBusy" @submit.prevent="savePortrait">
                            <fieldset :disabled="portraitBusy" style="border: 0; min-width: 0;">
                                <section class="portrait-section">
                                    <h4>我告诉小晚的</h4>
                                    <p class="portrait-hint">全部选填，由你手动维护，小晚只会参考、不会自动修改。</p>
                                    <div class="form-group">
                                        <label for="portrait-call-me">怎么称呼我</label>
                                        <input id="portrait-call-me" v-model="portraitDraft.manual.call_me" type="text"
                                            maxlength="32" placeholder="例如：阿明、老陈">
                                    </div>
                                    <div class="form-group">
                                        <label for="portrait-city">常住城市</label>
                                        <input id="portrait-city" v-model="portraitDraft.manual.city" type="text"
                                            maxlength="48" placeholder="例如：杭州（问天气时可直接参考）">
                                    </div>
                                    <div class="form-group">
                                        <label for="portrait-birthday">生日 / 年龄段</label>
                                        <input id="portrait-birthday" v-model="portraitDraft.manual.birthday" type="text"
                                            maxlength="32" placeholder="例如：05-18 或 00后">
                                    </div>
                                    <div class="form-group">
                                        <label for="portrait-occupation">职业 / 身份</label>
                                        <input id="portrait-occupation" v-model="portraitDraft.manual.occupation" type="text"
                                            maxlength="64" placeholder="例如：大三学生、后端开发">
                                    </div>
                                    <div class="form-group">
                                        <label for="portrait-preference">相处偏好 / 忌讳</label>
                                        <textarea id="portrait-preference" v-model="portraitDraft.manual.chat_preference"
                                            rows="2" maxlength="200" placeholder="例如：吐槽时先陪我聊，别急着给建议"></textarea>
                                    </div>
                                </section>

                                <section class="portrait-section">
                                    <h4>小晚记住的</h4>
                                    <p class="portrait-hint">随聊天自动积累的兴趣爱好、习惯和生活细节，可随时删改。</p>
                                    <div class="portrait-learned-bar">
                                        <label class="portrait-toggle" for="portrait-auto-learn">
                                            <input id="portrait-auto-learn" v-model="portraitDraft.auto_learn" type="checkbox">
                                            <span>聊天时自动记录新印象</span>
                                        </label>
                                        <button id="portrait-clear-learned" type="button" class="settings-button"
                                            :disabled="!portraitDraft.learned.length" @click="clearPortraitNotes">清空印象</button>
                                    </div>
                                    <ul v-if="portraitDraft.learned.length" id="portrait-learned-list" class="portrait-learned-list">
                                        <li v-for="(item, index) in portraitDraft.learned" :key="item" class="portrait-learned-item">
                                            <span>{{ item }}</span>
                                            <button type="button" class="portrait-remove-btn" aria-label="删除这条印象"
                                                title="删除这条印象" @click="removePortraitNote(index)">×</button>
                                        </li>
                                    </ul>
                                    <p v-else class="portrait-hint">小晚还没有记下新印象，多聊几句她会慢慢记住你。</p>
                                    <div class="portrait-add-row">
                                        <input id="portrait-new-note" v-model="portraitNewNote" type="text" maxlength="120"
                                            placeholder="也可以手动补充一条，例如：养了只橘猫" @keydown.enter.prevent="addPortraitNote">
                                        <button id="portrait-add-note" type="button" class="settings-button"
                                            @click="addPortraitNote">添加</button>
                                    </div>
                                </section>
                            </fieldset>
                            <div class="modal-btns">
                                <button id="cancel-portrait-btn" type="button" class="settings-button"
                                    :disabled="portraitBusy" @click="closePortrait">取消</button>
                                <button id="save-portrait-btn" type="submit" class="settings-button settings-button-primary"
                                    :disabled="portraitBusy">
                                    {{ portraitOperation === 'saving' ? '正在保存…' : '保存画像' }}
                                </button>
                            </div>
                        </form>
                    </div>
                </dialog>

                <dialog id="model-edit-modal" ref="modelDialog" class="modal" aria-labelledby="open-modify-btn" @cancel.prevent="closeModel">
                    <div class="modal-content">
                        <form id="model-config-form" ref="modelForm" autocomplete="off" novalidate
                            :aria-busy="modelBusy" @submit.prevent="configureModel(false)">
                            <div class="form-group">
                                <label for="modelSelect">选择模型厂商:</label>
                                <select id="modelSelect" v-model="merchant" class="settings-select" autofocus
                                    :disabled="modelBusy" @change="setProvider()">
                                    <option v-if="modelOptions?.providers.deepseek" value="deepseek">deepseek</option>
                                    <option v-if="modelOptions?.providers.tongyi" value="tongyi">通义千问</option>
                                </select>
                            </div>
                            <div class="form-group">
                                <label for="modelName">模型名:</label>
                                <select id="modelName" v-model="modelName" class="settings-select" required :disabled="modelBusy">
                                    <option v-for="name in provider?.names" :key="name" :value="name">{{ name }}</option>
                                </select>
                            </div>
                            <div class="form-group">
                                <div class="model-address-heading">
                                    <label for="model-base-url">接口地址:</label>
                                    <button id="model-address-help" type="button" class="settings-button"
                                        :disabled="modelBusy" @click="showAddressHelp">地址说明</button>
                                </div>
                                <input id="model-base-url" ref="addressInput" v-model="address" type="url" autocomplete="off"
                                    spellcheck="false" :disabled="modelBusy">
                            </div>
                            <div class="form-group">
                                <label for="model-api-key">API Key</label>
                                <div class="model-key-field">
                                    <input id="model-api-key" ref="keyInput" :type="keyVisible ? 'text' : 'password'"
                                        maxlength="1024" autocomplete="off" autocapitalize="none" autocorrect="off" spellcheck="false"
                                        :required="!hasSavedKey" :placeholder="hasSavedKey ? '********' : ''"
                                        :disabled="modelBusy" @beforeinput="discardAutofilledApiKey" @input="trackApiKey">
                                    <button type="button" id="toggle-model-key" class="settings-button"
                                        aria-controls="model-api-key" :aria-label="keyVisible ? '隐藏 API Key' : '显示 API Key'"
                                        :disabled="modelBusy" @click="toggleApiKey">
                                        {{ keyVisible ? '隐藏' : '显示' }}
                                    </button>
                                </div>
                            </div>
                            <div class="modal-btns">
                                <button type="button" id="cancel-modify-btn" class="settings-button" :disabled="modelBusy" @click="closeModel">取消</button>
                                <button type="button" id="test-model-btn" class="settings-button" :disabled="modelBusy" @click="configureModel(true)">
                                    {{ modelOperation === 'test' ? '正在测试…' : '测试连接' }}
                                </button>
                                <button type="submit" id="submit-model-btn" class="settings-button settings-button-primary" :disabled="modelBusy">
                                    {{ modelOperation === 'save' ? '正在验证并保存…' : isAdmin ? '保存并切换' : '保存模型' }}
                                </button>
                            </div>
                        </form>
                    </div>
                </dialog>
            </div>
        </div>
    </dialog>
</template>
