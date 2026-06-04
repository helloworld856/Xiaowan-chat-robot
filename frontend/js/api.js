
// 自动使用当前页面的 host
const base_url = window.location.origin;
console.log('base_url:',base_url);

//只负责取数据，不碰DOM、localstorage

//向接口发送请求,await是等待网络请求完成，否则代码会继续执行而不等服务器响应
export async  function versionAPI(){
    const res = await fetch(`${base_url}/version`);//fetch是异步操作

    //如果服务器出错则报错
    if (!res.ok) throw new Error("version接口失败");
    return await res.json();
}

export async function chatAPI(userInput){
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
export async function historyAPI(num, front=true){
    try{
        const res = await fetch(
            `${base_url}/history`,
            {
                method:'post',
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ num: num, front: front })
            }
        )
        if(!res.ok){
            throw new Error("history接口失败");
        };
        //读取服务器返回的JSON数据
        return await res.json();
    }
    catch(e){
        console.log('history接口发生错误:', e);
    }
}

//请求人格
export async function personaAPI() {
    const res = await fetch(`${base_url}/persona`);
    if (!res.ok) throw new Error("persona 接口失败");
    return await res.json();
}

//模型配置
export async function modelAPI(model){
    console.log('模型厂商:', model.model_merchant);
    console.log('API密钥: ********************');
    console.log('模型名:', model.model_name);
    const res = await fetch(
        `${base_url}/model`,//请求的url
         {
            method:"post",//请求的方法
            headers: { "Content-Type": "application/json" },//请求头，告诉服务器发送的是什么格式的数据
            body: JSON.stringify({
                    model_merchant: model.model_merchant||'',
                    api_key: model.api_key||'',
                    model_name:model.model_name||''
                })//请求体，转换成json格式
         }
    );
    if(!res.ok) throw new Error("model接口失败");

    //读取服务器返回的JSON数据
    return await res.json();
} 
