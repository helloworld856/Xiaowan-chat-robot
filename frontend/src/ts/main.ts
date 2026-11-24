//主入口
import {
    updateUi,
    lockSendBtn,
    unlockSendBtn,
    showAlert,
    resetPopup,
    windowAlertContainer,
    windowAlertP
} from './ui.ts';

import {personaAPI, historyAPI, AuthRequiredError, CancelledRequestError, getRequestGeneration} from './api.ts';
import {showChatHistory} from './utils.ts';
import {addEventToUi} from './addevent.ts';
import {personaConfig} from './global_config.ts';
import {loadSettings, resetSettings, updateSettingsPermissions} from './settings.ts';
import {initializeAuth} from './auth.ts';
import {loadProfile, resetProfile} from './profile.ts';
import {closeConfirmation} from './confirm.ts';
import {clearChatInput} from './composer.ts';

const loading = document.getElementById('loading') as HTMLDivElement;
const app = document.getElementById('app-shell')!;

async function loadPersona() {
    const generation = getRequestGeneration();
    const persona = await personaAPI();
    if (generation !== getRequestGeneration()) return;
    personaConfig.persona_valid = true;
    personaConfig.persona = {...persona};
    updateUi(personaConfig.persona);
}

async function loadHistory() {
    const generation = getRequestGeneration();
    const result = await historyAPI(0);
    if (generation === getRequestGeneration() && result?.history_messages) {
        showChatHistory(result.history_messages);
    }
}

async function init(generation: number){
    const warnings: string[] = [];
    // 先加载个人资料，保证随后加载的人设和历史使用当前账号的头像。
    const steps = [
        {name: '个人资料', load: loadProfile},
        {name: '角色信息', load: loadPersona},
        {name: '应用设置', load: loadSettings},
        {name: '历史对话', load: loadHistory}
    ];
    // 单个步骤失败后继续启动，最后集中提示。
    for (const step of steps) {
        if (generation !== getRequestGeneration()) throw new CancelledRequestError();
        try {
            await step.load();
        } catch (error) {
            if (generation !== getRequestGeneration()) throw new CancelledRequestError();
            if (error instanceof AuthRequiredError || error instanceof CancelledRequestError) throw error;
            console.warn(`${step.name}加载失败`, error);
            warnings.push(`${step.name}加载失败，请检查后端、网络或相关配置。`);
        }
    }
    return warnings;
}

async function initializeChat() {
    const generation = getRequestGeneration();
    app.inert = true;
    updateSettingsPermissions();
    lockSendBtn();
    loading.classList.remove('close');
    try {
        const warnings = await init(generation);
        if (generation === getRequestGeneration() && warnings.length) showAlert(warnings.join('\n'));
    } catch (error) {
        if (!(error instanceof AuthRequiredError || error instanceof CancelledRequestError)) throw error;
    } finally {
        // 旧账号的加载结束后，由当前账号自己的初始化流程恢复界面交互。
        if (generation === getRequestGeneration()) {
            unlockSendBtn();
            loading.classList.add('close');
            app.inert = false;
        }
    }
}

// 退出时清除上个账号的草稿、历史、回顾和资料展示。
function clearChat() {
    closeConfirmation();
    resetPopup(windowAlertContainer);
    windowAlertP.textContent = '';
    resetProfile();
    resetSettings();
    document.getElementById('chatBox')!.replaceChildren();
    clearChatInput();
    (document.getElementById('review-search') as HTMLInputElement).value = '';
    document.getElementById('review-list')!.replaceChildren();
    document.getElementById('current-username')!.textContent = '';
    document.getElementById('comeToBottom')!.style.display = 'none';
    window.BOT_AVATAR = '';
    performance.clearMeasures('xiaowan.chat.first-reply');
    personaConfig.persona_valid = false;
    personaConfig.persona = {BOT_NAME: '', BOT_AVATAR: '', USER_AVATAR: '', BOT_BIRTHDAY: '', BOT_BIRTHPLACE: ''};
}

lockSendBtn();
addEventToUi();
await initializeAuth(initializeChat, clearChat);
