// 给组件加事件
import {
    sendBtn,
    inputBox,
    comeToBottom,
    sidebarBtn,
    sidebar,
    menuBtn,
    menu,
    themeInput,
    menuSelect,
    windowAlertContainer,
    windowAlertP
}from './ui.js'
import { sendMessage } from './chat.js';
import {switchTheme} from './utils.js'
import {modelAPI} from './api.js'
import {saveModel} from './storage.js'
import {modelConfig} from './global_config.js'

export function  addEventToUi(){
    //发送按钮
    sendBtn.onclick = () => sendMessage();

    //文本输入框
    inputBox.addEventListener('keypress', e=>{
        if(e.key === 'Enter' && !sendBtn.disabled){
            sendMessage();
        }
    });

    //判断是否显示回到底部按钮
    chatBox.addEventListener('scroll', function(){
        var comeToBottom = document.getElementById('comeToBottom');
        const isAtBottom = chatBox.scrollTop + chatBox.clientHeight >= chatBox.scrollHeight - 120;
        if (isAtBottom){
            comeToBottom.style.display = 'none';
        }
        else{
            comeToBottom.style.display = 'block';
        }
    })
    //点击回到底部按钮，回到底部
    comeToBottom.addEventListener('click' ,function(){
        //滚动到底部
        chatBox.scrollTo({
            top: chatBox.scrollHeight,
            behavior: 'smooth' //平滑滚动
        });
        // 隐藏按钮
        comeToBottom.style.display = 'none';
    })

    //sidebar.style.display 只能读取通过 JavaScript 或内联样式（inline style）设置的 display 值，无法读取 CSS 样式表中定义的初始值。
    //侧边栏唤起按钮
    sidebarBtn.addEventListener('click', function (){
        const sidebarStyle = window.getComputedStyle(sidebar)
        if (sidebarStyle.display === 'none'){//如果侧边栏没有显示，就显示
            //将侧边栏唤起按钮置为激活态
            sidebarBtn.classList.add('active');
        }
        else{//否则隐藏侧边栏
            sidebarBtn.classList.remove('active');

        }
    })

    //菜单唤起按钮
    menuBtn.addEventListener('click', function(e){
        e.stopPropagation(); // 阻止事件冒泡到 document
        const menuStyle = window.getComputedStyle(menu);
        if (menuStyle.display === 'flex') {
            // 如果已打开，就关闭
            menuBtn.classList.remove('active');
        }else{
            // 如果未打开，就打开
            menuBtn.classList.add('active');
        }
    })

    //打开主题选择菜单的按钮
    menuSelect[2].addEventListener('click', function (){
        const theme_select = document.querySelector('#menu .theme-select');//主题选择菜单
        const theme_selectStyle = window.getComputedStyle(theme_select);
        if (theme_selectStyle.display === 'none'){
            menuSelect[2].classList.add('active');
        }
        else{
            menuSelect[2].classList.remove('active');
        }

    // 点击菜单内部（包括里面的按钮）时不关闭菜单
    menu.addEventListener('click', function(e) {
            e.stopPropagation(); // 阻止事件冒泡到 document
        })
    })

    // 点击页面任意其他地方 → 关闭菜单
    document.addEventListener('click', (e) => {
        menuBtn.classList.remove('active');
            menuSelect.forEach(radio=>{
            radio.classList.remove('active');
        })
    })

    //监听主题选择框
    themeInput.forEach(radio=>{
        radio.addEventListener('change', function(){
            if (this.checked){//如果这个被选中
                const value = this.value;
                console.log('切换主题...');
                switchTheme(value);
            }
        })
    })

    // --- 设置界面交互逻辑 ---
    const settingContainer = document.querySelector('#setting-container');
    const modelEditModal = document.getElementById('model-edit-modal');
    
    // 更新展示信息的函数
    const updateDisplayInfo = () => {
        document.getElementById('display-merchant').innerText = modelConfig.model.model_merchant || '未配置';
        document.getElementById('display-model').innerText = modelConfig.model.model_name || '未配置';
        document.getElementById('display-key').innerText = modelConfig.model.api_key ? '********' : '未设置';
    };

    // 1. 打开设置主界面
    menuSelect[1].addEventListener('click', (e) => {
        e.stopPropagation();
        settingContainer.classList.add('open');
        updateDisplayInfo();
    });

    // 2. 关闭设置主界面
    document.querySelector('.close-setting').addEventListener('click', () => {
        settingContainer.classList.remove('open');
        modelEditModal.classList.remove('open'); // 同时关闭可能打开的弹窗
    });

    // 3. 打开修改配置弹窗
    document.getElementById('open-modify-btn').addEventListener('click', () => {
        modelEditModal.classList.add('open');
        
        // 初始化表单值为当前配置
        const modelSelect = document.getElementById('modelSelect');
        const apiKeyInput = document.getElementById('apiKeyInput');
        const modelNameSelect = document.getElementById('modelName');

        modelSelect.value = modelConfig.model.model_merchant || 'deepseek';
        modelSelect.dispatchEvent(new Event('change')); // 触发联动更新模型名列表
        apiKeyInput.value = modelConfig.model.api_key || '';
        modelNameSelect.value = modelConfig.model.model_name || '';
    });

    // 4. 取消修改
    document.getElementById('cancel-modify-btn').addEventListener('click', () => {
        modelEditModal.classList.remove('open');
    });

    // 5. 模型厂商与模型名联动逻辑
    const modelSelect = document.getElementById('modelSelect');
    modelSelect.addEventListener('change', () => {
        const modelNameSelect = document.getElementById('modelName');
        const merchant = modelSelect.value;
        
        let options = ['deepseek-v4-flash', 'deepseek-v4-pro'];
        if(merchant === 'tongyi'){
            options = ['qwen3-max', 'qwen-plus', 'qwen-turbo', 'qwen-flash'];
        }

        modelNameSelect.innerHTML = options.map(opt => `<option value="${opt}">${opt}</option>`).join('');
    });

    // 6. 提交修改并验证
    const modelSubmitBtn = document.getElementById('submit-model-btn');
    const validLoader = document.querySelector('.valid-loader');
    const modalBtns = document.querySelector('.modal-btns');

    modelSubmitBtn.addEventListener('click', async () => {
        const merchant = document.getElementById('modelSelect').value;
        const apiKey = document.getElementById('apiKeyInput').value.trim();
        const modelName = document.getElementById('modelName').value;

        if(!apiKey){
            windowAlertP.innerText = '请输入API-KEY!';
            windowAlertContainer.classList.add('open');
            return;
        }

        // 按钮进入加载状态
        modelSubmitBtn.disabled = true;
        modelSubmitBtn.innerText = '验证中...';

        validLoader.classList.add('open');
        modalBtns.classList.add('close');

        try {
            const modelData = {
                model_merchant: merchant,
                api_key: apiKey,
                model_name: modelName
            };

            const res = await modelAPI(modelData);

            if(res.status){
                // 验证成功：同步全局配置
                modelConfig.model = { ...modelData };
                modelConfig.model_valid = true;
                saveModel(modelConfig);
                
                // 更新主界面的展示信息
                updateDisplayInfo();
                
                windowAlertP.innerText = '模型配置验证通过并已保存！';
                windowAlertContainer.classList.add('open');

                modelEditModal.classList.remove('open'); // 关闭修改弹窗
                const span = document.querySelector('#setting-container .setting-head span');
                span.classList.remove('show-after');
            }
            else{
                windowAlertP.innerText = '验证失败: ' + (res.info || '原因未知');
                windowAlertContainer.classList.add('open');
            }
        } catch (error) {
            console.error('验证过程出错:', error);
            windowAlertP.innerText = '请求失败，请检查网络或后端服务';
            windowAlertContainer.classList.add('open');
        } finally {
            modelSubmitBtn.disabled = false;
            modelSubmitBtn.innerText = '确认修改';
            validLoader.classList.remove('open');
            modalBtns.classList.remove('close');
        }
    });

    //提示弹窗
    const windowAlertBtn = document.querySelector('.alert-window .alert-window-button button');
    windowAlertBtn.addEventListener('click', (e)=>{
        const windowAlert = document.querySelector('#alert-window-container');
        windowAlert.classList.remove('open');
    })
}
