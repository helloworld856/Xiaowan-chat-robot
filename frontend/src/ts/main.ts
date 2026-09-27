//主入口
import {loadModel} from './storage.ts';
import {
    updateUi,
    lockSendBtn,
    unlockSendBtn,
    themeInput
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
    console.log('最终人格:', personaConfig.persona);
    updateUi(personaConfig.persona);
}

async function restoreModelConfig() {
    console.log('检查模型配置信息...');
    const savedConfig = loadModel();
    if (!savedConfig?.model) {
        console.log('无缓存模型配置');
        return;
    }

    try {
        const result = await modelAPI(savedConfig.model);
        console.log('模型验证结果:', result);
        modelConfig.model_valid = Boolean(result.status);
        if (result.status) {
            modelConfig.model = {...savedConfig.model};
            console.log('模型配置验证通过，当前模型:', modelConfig.model.model_name);
        } else {
            console.warn('缓存模型配置验证失败:', result.info);
        }
    } catch (error) {
        console.error('模型验证请求出错:', error);
        modelConfig.model_valid = false;
    }
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
    try {
        await loadPersona();
        await restoreModelConfig();
    } catch (error) {
        console.warn("无法连接接口", error);
    }
    await loadHistory();
}

lockSendBtn();
loading.classList.remove('close');
addEventToUi();
loadSavedTheme();
await init();
unlockSendBtn();
loading.classList.add('close');
