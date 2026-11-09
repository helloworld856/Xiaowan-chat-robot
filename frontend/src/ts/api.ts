
import type { ModelInfo, ChatResponse } from './global_config.ts';
import type {Preferences} from './preferences.ts';

// 自动使用当前页面的 host
const base_url = window.location.origin;
console.log('base_url:',base_url);

//只负责取数据，不碰DOM、localstorage

export async function chatAPI(userInput: string): Promise<ChatResponse>{
    const res = await fetch(
        `${base_url}/chat`,//请求的url
         {
            method:"post",//请求的方法
            headers: { "Content-Type": "application/json" },//请求头，告诉服务器发送的是什么格式的数据
            body: JSON.stringify({ user_input: userInput })//请求体，转换成json格式
         }
    );
    if(!res.ok) throw new Error("chat接口失败");

    //读取服务器返回的JSON数据
    return await res.json();
}

//获取历史对话记录
export async function historyAPI(num: number, front=true){
        const res = await fetch(
            `${base_url}/history`,
            {
                method:'post',
                signal: AbortSignal.timeout(15000),
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ num: num, front: front })
            }
        )
        if(!res.ok)
            throw new Error("history接口失败");

        //读取服务器返回的JSON数据
        return await res.json();
}

//请求人格
export async function personaAPI() {
    const res = await fetch(`${base_url}/persona`, {signal: AbortSignal.timeout(15000)});
    if (!res.ok) throw new Error("persona 接口失败");
    return await res.json();
}

//模型配置
export async function modelAPI(model: ModelInfo, testOnly = false){
    console.log('模型厂商:', model.model_merchant);
    console.log('模型名:', model.model_name);
    const res = await fetch(
        `${base_url}/model${testOnly ? '/test' : ''}`,//请求的url
         {
            method:"post",//请求的方法
            signal: AbortSignal.timeout(30000),
            headers: { "Content-Type": "application/json" },//请求头，告诉服务器发送的是什么格式的数据
            body: JSON.stringify({
                    model_merchant: model.model_merchant||'',
                    model_name:model.model_name||'',
                    base_url:model.base_url||''
                })//请求体，转换成json格式
         }
    );
    if(!res.ok) throw new Error("model接口失败");

    //读取服务器返回的JSON数据
    return await res.json();
} 

export interface SavedSettings { model: ModelInfo | null; preferences: Preferences; }

export async function settingsAPI(preferences?: Preferences): Promise<SavedSettings> {
    const response = await fetch(`${base_url}/settings`, {
        method: preferences ? 'POST' : 'GET',
        headers: {'Content-Type': 'application/json'},
        signal: AbortSignal.timeout(15000),
        ...(preferences ? {body: JSON.stringify(preferences)} : {})
    });
    if (!response.ok) throw new Error(preferences ? '设置保存失败，请检查后端和配置文件。' : '设置读取失败，可在设置页重新读取。');
    return response.json();
}
