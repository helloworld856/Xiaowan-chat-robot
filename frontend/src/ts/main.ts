//主入口
import {
    loadModel,
} from './storage.ts';
import {
    chatBox,
    updateUi,
    lockSendBtn,
    unlockSendBtn,
    themeInput
} from './ui.ts';

import {personaAPI, modelAPI, historyAPI} from './api.ts'
import {showChatHistory, switchTheme} from './utils.ts'
import {addEventToUi}from './addevent.ts'
import { modelConfig, personaConfig } from './global_config.ts';


lockSendBtn();
const loading = document.getElementById('loading') as HTMLDivElement;
loading.classList.remove('close');

async function init(){
    try{
        console.log('拉取人格...');

        //拉取人格
        const res = await personaAPI();//{}就相当于序列解包
        console.log('拉取到人格:',res);
        personaConfig.persona_valid = true;
        personaConfig.persona = {...res};
        console.log('最终人格:', personaConfig.persona);

        //更新ui
        updateUi(personaConfig.persona);

        console.log('检查模型配置信息...');
        //获取浏览器缓存模型配置信息
        const savedConfig = loadModel(); // 使用 storage.ts 的 loadModel，它会自动 JSON.parse
        
        if (savedConfig && savedConfig.model) {
            try {
                // 判断模型配置信息是否有效
                const res = await modelAPI(savedConfig.model);
                console.log('模型验证结果:', res);
                if (res.status) {
                    // 有效则记录模型配置信息
                    modelConfig.model_valid = true;
                    modelConfig.model = { ...savedConfig.model };
                    console.log('模型配置验证通过，当前模型:', modelConfig.model.model_name);
                } else {
                    console.warn('缓存模型配置验证失败:', res.info);
                    modelConfig.model_valid = false;
                }
            } catch (error) {
                console.error('模型验证请求出错:', error);
                modelConfig.model_valid = false;
            }
        } else {
            console.log('无缓存模型配置');
        }


    }catch(error){
        console.warn("无法连接接口", error);
    }

    //获取历史对话
    const res = await historyAPI(0);
    if (res&&res.history_messages){
        //显示历史对话
        showChatHistory(chatBox, res.history_messages); 
    }
}

//为组件添加事件
addEventToUi();

//加载保存的主题（默认theme0）
const theme = localStorage.getItem('theme') || 'theme0';
switchTheme(theme);
themeInput.forEach(radio=>{
    radio.checked = (radio.value === theme);
})

//版本检查及人格初始化
await init();

unlockSendBtn();

// 关闭加载画面
loading.classList.add('close');