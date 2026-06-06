//聊天气泡创建
import { chatBox, scrollToBottom} from './ui.js';
import {modelConfig} from './global_config.js'


export async function addUserMessage(text){
    console.log('用户消息:'+text);
    const div = document.createElement("div");
    const div_avatar = document.createElement("div");//显示头像
    const div_tri = document.createElement("div");//这个插在气泡和头像之间，用来实现气泡上三角形的效果
    const div_text = document.createElement("div");//显示消息
    const img = document.createElement('img');

    img.src = window.USER_AVATAR;
    img.alt = '你的头像';
    div_text.textContent = text;

    //组装元素
    div.appendChild(div_text);
    div.appendChild(div_tri);
    div_avatar.appendChild(img);
    div.appendChild(div_avatar);

    //设置class
    div.className = 'user_message';
    div_avatar.className = 'user_avatar';
    div_text.className = 'user_bubble';
    div_tri.className = 'user_triangle';

    //加入聊天框
    chatBox.appendChild(div);
}


export async function addAssistantMessage(messages){//助手回复可能有多条消息，每条消息都一个头像加消息,并且用div包裹
    //发送间隔, 基础为1s,发送字符数每增加1个，延长0.1秒，最多5秒
    var baseSendInterval = 1000;

    //保存滚动状态
    const scrollHeightBefore = chatBox.scrollHeight;//所有内容的总高度
    const scrollTopBefore = chatBox.scrollTop;//已经滚动上去的高度
    const clientHeight = chatBox.clientHeight;//可视区域的高度
    for (var i=0;i<messages.length;i++) {
        const div = document.createElement("div");
        const div_bag = document.createElement("div");//因为助手有多条消息，并且每个消息配一个头像，所以再使用一个bag把这些头像消息对再包裹,每个bag再放到一个div里
        const div_avatar = document.createElement("div");//显示头像
        const div_text = document.createElement("div");
        const div_tri = document.createElement("div");
        const img = document.createElement('img');

        img.src = window.BOT_AVATAR;
        img.alt = '对方的头像';
        div_text.textContent = messages[i];

        div_avatar.appendChild(img);
        div_bag.appendChild(div_avatar);
        div_bag.appendChild(div_tri);
        div_bag.appendChild(div_text);
        div.appendChild(div_bag);

        //设置class
        div_bag.className = 'assistant_bag';
        div_avatar.className = 'assistant_avatar';
        div_text.className = 'assistant_bubble';
        div_tri.className = 'assistant_triangle';
        div.className = 'assistant_message';

        //加入聊天框
        chatBox.appendChild(div);

        //检查判断是否需要回到底部
        if (scrollTopBefore + clientHeight >= scrollHeightBefore - 2){
            scrollToBottom();
        }
        const t = baseSendInterval + 100*messages[i].length;

        console.log('助手消息：', messages[i]);
        if (i!==messages.length-1){
            console.log(`下一条助手消息于${t}ms后发送`);
            await new Promise(r => setTimeout(r, t<=5000?t:5000));
        }
    }
}

//显示历史聊天记录
export function showChatHistory(chatBox, chatHistory){
    chatHistory.forEach(msg => {
        //用户容器
        const div_user = document.createElement("div");
        const div_user_avatar = document.createElement("div");//显示用户头像
        const div_user_text = document.createElement("div");//显示用户消息
        const div_tri = document.createElement("div");//三角形效果
        const img = document.createElement('img');//头像
        //设置用户头像和消息
        img.src = window.USER_AVATAR;
        img.alt = '你的头像';
        div_user_text.textContent = msg['user'];
        //设置class
        div_user.className = 'user_message';
        div_user_avatar.className = 'user_avatar';
        div_user_text.className = 'user_bubble';//消息气泡
        div_tri.className = 'user_triangle';
        //加入用户头像和消息
        div_user_avatar.appendChild(img);
        div_user.appendChild(div_user_text);
        div_user.appendChild(div_tri);
        div_user.appendChild(div_user_avatar);

        //加入聊天框
        chatBox.appendChild(div_user);

        //助手容器
        msg['assistant'].forEach(amsg =>{//用户消息有多条，所以要遍历
            const div_assistant = document.createElement("div");
            div_assistant.className = 'assistant_message';

            const div_assistant_avatar = document.createElement("div");
            const div_assistant_text = document.createElement("div");
            const div_bag = document.createElement("div");
            const div_tri = document.createElement("div");
            const img  = document.createElement('img');

            img.src = window.BOT_AVATAR;
            img.alt = '对方的头像';
            div_assistant_text.textContent = amsg;

            //设置class
            div_bag.className = "assistant_bag";
            div_assistant_avatar.className = 'assistant_avatar';
            div_assistant_text.className = 'assistant_bubble';
            div_tri.className = 'assistant_triangle';

            //加入容器
            div_assistant_avatar.appendChild(img);
            div_bag.appendChild(div_assistant_avatar);
            div_bag.append(div_tri);
            div_bag.appendChild(div_assistant_text);
            div_assistant.appendChild(div_bag);

            //加入聊天框
            chatBox.appendChild(div_assistant);
        })
    })
    scrollToBottom();
}

//设置主题
export function switchTheme(theme){
    document.documentElement.className = theme;
    localStorage.setItem('theme', theme);
    console.log('主题已切换：', theme);
}

export function model_invalid(){
    if (!modelConfig.model_valid){
        window.alert('请配置有效的模型！');
        return false;
    }
    return true;
}