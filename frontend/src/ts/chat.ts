//发送消息并获得回复

import {chatAPI} from './api.ts'
import { lockSendBtn, unlockSendBtn, scrollToBottom, sendBtn, showTypingIndicator, hideTypingIndicator } from './ui.ts';
import { addUserMessage, addAssistantMessage} from './utils.ts';
import {modelConfig} from './global_config.ts'


export async function sendMessage() {
    if (sendBtn.disabled) return;
    if(!modelConfig.model_valid){
        window.alert('请先配置有效的模型!');
        return ;
    }
    
    const message:{conversation_round:number, user:string, assistant:string[]} = {
            conversation_round: 0,
            user: '',
            assistant: [],
        }

    //获取用户输入的内容
    const input = document.getElementById("userInput") as HTMLInputElement; //断言
    const text = input.value.trim();

    //如果用户没有输入内容就什么也不做
    if (!text) return;
    lockSendBtn(); // 在第一个 await 前锁住，防止重复发送。

    //记录用户输入
    message.user = text;

    //清空输入框，等待期间仍可以输入下一条消息
    input.value = '';

    try {
        await addUserMessage(text);
        showTypingIndicator();
        requestAnimationFrame(() => scrollToBottom());

        //请求回复
        const data = await chatAPI(text);

        //记录助手回复和轮次
        message.assistant = data.response;
        message.conversation_round = data.conversation_round;

        //把回复加入聊天框
        hideTypingIndicator();
        await addAssistantMessage(data.response);

    } catch (err) {
        hideTypingIndicator();
        if (err instanceof Error) {
            window.alert("❌ 错误: " + err.message);
        } else {
            // 如果抛出的不是 Error 对象（例如字符串、数字），这里统一处理
            window.alert("❌ 发生未知错误");
        }
    } finally {
        hideTypingIndicator();
        //无论结果如何，都把发送按钮解开
        unlockSendBtn();
        //inputBox.focus();//光标回到输入框
    }
}


