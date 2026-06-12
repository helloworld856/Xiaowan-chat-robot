//export把一个变量 / 函数“借给别的 JS 文件用”，这样别的文件可以import

//清空人格
// export function clearPersona(){
//     console.log('清空人格');
//     localStorage.removeItem('persona');
// }

//保存人格
// export function savePersona(persona){
//     localStorage.setItem('persona', JSON.stringify(persona));
// }

//加载人格
// export function loadPersona(){
//     return JSON.parse(localStorage.getItem("persona") || "null");
// }


//保存模型配置
export function saveModel(modelConfig: {model_valid: boolean, model: {model_name: string, model_merchant: string}}){
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


