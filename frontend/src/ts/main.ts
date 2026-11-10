//主入口
import {loadModel} from './storage.ts';
import {
    updateUi,
    lockSendBtn,
    unlockSendBtn,
    themeInput,
    showAlert
} from './ui.ts';

import {personaAPI, modelAPI, historyAPI, AuthRequiredError, CancelledRequestError, getRequestGeneration} from './api.ts';
import {showChatHistory, switchTheme} from './utils.ts';
import {addEventToUi} from './addevent.ts';
import { modelConfig, personaConfig } from './global_config.ts';
import {loadSettings} from './settings.ts';
import {initializeAuth} from './auth.ts';
import {loadProfile, resetProfile} from './profile.ts';

const loading = document.getElementById('loading') as HTMLDivElement;

async function loadPersona() {
    console.log('拉取人格...');
    const persona = await personaAPI();
    console.log('拉取到人格:', persona);
    personaConfig.persona_valid = true;
    personaConfig.persona = {...persona};
    updateUi(personaConfig.persona);
}

async function restoreModelConfig() {
    console.log('检查模型配置信息...');
    const savedConfig = modelConfig.model.model_name ? {model: modelConfig.model} : loadModel();
    if (!savedConfig?.model) {
        console.log('无缓存模型配置');
        return;
    }

    const result = await modelAPI(savedConfig.model);
    modelConfig.model_valid = Boolean(result.status);
    if (!result.status) throw new Error(result.info || '模型配置验证失败');
    modelConfig.model = {...savedConfig.model};
    console.log('模型配置验证通过，当前模型:', modelConfig.model.model_name);
}

async function loadHistory() {
    const result = await historyAPI(0);
    if (result?.history_messages) {
        showChatHistory(result.history_messages);
    }
}

function loadSavedTheme() {
    const theme = localStorage.getItem('theme') || 'theme0';
    switchTheme(theme);
    themeInput.forEach(radio => {
        radio.checked = radio.value === theme;
    });
}

async function init(generation: number){
    const warnings: string[] = [];
    const steps = [
        {name: '个人资料', load: loadProfile},
        {name: '角色信息', load: loadPersona},
        {name: '应用设置', load: loadSettings},
        {name: '模型配置', load: restoreModelConfig},
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
    lockSendBtn();
    loading.classList.remove('close');
    try {
        const warnings = await init(generation);
        if (generation === getRequestGeneration() && warnings.length) showAlert(warnings.join('\n'));
    } catch (error) {
        if (!(error instanceof AuthRequiredError || error instanceof CancelledRequestError)) throw error;
    } finally {
        if (generation === getRequestGeneration()) {
            unlockSendBtn();
            loading.classList.add('close');
        }
    }
}

function clearChat() {
    resetProfile();
    document.getElementById('chatBox')!.replaceChildren();
    (document.getElementById('userInput') as HTMLTextAreaElement).value = '';
    modelConfig.model_valid = false;
    modelConfig.model = {model_merchant: '', model_name: '', base_url: ''};
    personaConfig.persona_valid = false;
    personaConfig.persona = {BOT_NAME: '', BOT_AVATAR: '', USER_AVATAR: '', BOT_BIRTHDAY: '', BOT_BIRTHPLACE: ''};
}

lockSendBtn();
addEventToUi();
loadSavedTheme();
await initializeAuth(initializeChat, clearChat);
