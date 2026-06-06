//发送消息并获得回复

import {chatAPI} from './api.js'
import { lockSendBtn, unlockSendBtn, scrollToBottom } from './ui.js';
import { addUserMessage, addAssistantMessage} from './utils.js';
import {modelConfig} from './global_config.js'


export async function sendMessage() {
    if(!modelConfig.model_valid){
        window.alert('请先配置有效的模型!');
        return ;
    }
    
    const message = {
            conversation_round: null,
            user: null,
            assistant: null,
        }

    //获取用户输入的内容
    const input = document.getElementById("userInput");
    const text = input.value.trim();

    //如果用户没有输入内容就什么也不做
    if (!text) return;

    //记录用户输入
    message.user = text;

    //把用户消息加入聊天框
    await addUserMessage(text);

    // 等DOM渲染完再滚动（用 requestAnimationFrame 确保渲染完成）
    requestAnimationFrame(() => scrollToBottom());

    //清空输入框，并在得到回复前禁止发送，但可以输入
    input.value = '';
    lockSendBtn();

    try {
        //请求回复
        const data = await chatAPI(text);

        //记录助手回复和轮次
        message.assistant = data.response;
        message.conversation_round = data.conversation_round;

        //把回复加入聊天框
        await addAssistantMessage(data.response);

    } catch (err) {
        //如果网络或服务器出现错误，显示错误
        window.alert("❌ 错误: " + err.message);
    } finally {
        //无论结果如何，都把发送按钮解开
        unlockSendBtn();
        //inputBox.focus();//光标回到输入框
    }
}


