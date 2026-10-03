//主入口
import {loadModel} from './storage.ts';
import {
    updateUi,
    lockSendBtn,
    unlockSendBtn,
    themeInput,
    showAlert
} from './ui.ts';

import {personaAPI, modelAPI, historyAPI} from './api.ts';
import {showChatHistory, switchTheme} from './utils.ts';
import {addEventToUi} from './addevent.ts';
import { modelConfig, personaConfig } from './global_config.ts';

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
    const savedConfig = loadModel();
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

async function init(){
    const warnings: string[] = [];
    const steps = [
        {name: '角色信息', load: loadPersona},
        {name: '模型配置', load: restoreModelConfig},
        {name: '历史对话', load: loadHistory}
    ];
    // 单个步骤失败后继续启动，最后集中提示。
    for (const step of steps) {
        try {
            await step.load();
        } catch (error) {
            console.warn(`${step.name}加载失败`, error);
            warnings.push(`${step.name}加载失败，请检查后端、网络或相关配置。`);
        }
    }
    return warnings;
}

lockSendBtn();
loading.classList.remove('close');
let warnings: string[] = [];
try {
    addEventToUi();
    loadSavedTheme();
    warnings = await init();
} finally {
    unlockSendBtn();
    loading.classList.add('close');
}
if (warnings.length) showAlert(warnings.join('\n'));
