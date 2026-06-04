//主入口
import {
    versionAPI,
 } from './api.js';
import {
    getVersion,
    setVersion,
    loadPersona,
    savePersona,
    clearPersona,
    loadModel,
} from './storage.js';
import {
    chatBox,
    updateUi,
    lockSendBtn,
    unlockSendBtn,
    themeInput
} from './ui.js';

import {personaAPI, modelAPI, historyAPI} from './api.js'
import {showChatHistory, switchTheme} from './utils.js'
import {addEventToUi}from './addevent.js'
import { modelConfig } from './model_config.js';


lockSendBtn();
const loading = document.getElementById('loading');
loading.classList.remove('close');

//人格信息
let persona = loadPersona();//首次加载时先使用localStorage里的persona

updateUi(persona);

async function init(){
    try{
        console.log('检查版本...');
        const {version} = await versionAPI();//{}就相当于序列解包
        //版本不一样就清空历史对话并重新请求人格
        if(version!==getVersion()){
            console.log('版本不一致！');

            //保存新版本号
            setVersion(version);

            //清空人格
            clearPersona();

            //拉取新人格
            persona = await personaAPI();
            savePersona(persona);

            //更新ui
            updateUi(persona);
        }

        console.log('检查模型配置信息...');
        //获取浏览器缓存模型配置信息
        const savedConfig = loadModel(); // 使用 storage.js 的 loadModel，它会自动 JSON.parse
        
        if (savedConfig && savedConfig.model && savedConfig.model.api_key) {
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

console.log('最终人格:', persona);
unlockSendBtn();

// 关闭加载画面
loading.classList.add('close');