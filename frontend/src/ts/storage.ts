import type { ModelConfig } from './global_config.ts';

//保存模型配置
export function saveModel(modelConfig: ModelConfig){
    localStorage.setItem('modelConfig', JSON.stringify(modelConfig));
}

//加载模型配置
export function loadModel(){
    console.log('加载模型配置...');
    const data = localStorage.getItem('modelConfig');
    try {
        return JSON.parse(data || "{}");
    } catch (e) {
        console.error('模型配置解析失败，可能是数据格式错误:', e);
        // 如果解析失败（比如存了 "[object Object]"），返回空对象并清空坏数据
        localStorage.removeItem('modelConfig');
        return {};
    }
}

