// 给组件绑定事件
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
    windowAlertP,
    chatBox,
    openPopup,
    closePopup
} from './ui.ts';
import {sendMessage} from './chat.ts';
import {switchTheme} from './utils.ts';
import {modelAPI} from './api.ts';
import {saveModel} from './storage.ts';
import {modelConfig} from './global_config.ts';

const MODEL_OPTIONS: Record<string, string[]> = {
    deepseek: ['deepseek-v4-flash', 'deepseek-v4-pro'],
    tongyi: ['qwen3-max', 'qwen-plus', 'qwen-turbo', 'qwen-flash']
};

function bindChatEvents() {
    sendBtn.onclick = () => sendMessage();

    inputBox.addEventListener('keypress', event => {
        if (event.key === 'Enter' && !sendBtn.disabled) sendMessage();
    });

    chatBox.addEventListener('scroll', () => {
        const isAtBottom = chatBox.scrollTop + chatBox.clientHeight >= chatBox.scrollHeight - 120;
        comeToBottom.style.display = isAtBottom ? 'none' : 'block';
    });

    comeToBottom.addEventListener('click', () => {
        chatBox.scrollTo({
            top: chatBox.scrollHeight,
            behavior: 'smooth'
        });
        comeToBottom.style.display = 'none';
    });
}

function bindSidebarEvents() {
    sidebarBtn.addEventListener('click', () => {
        const isClosed = window.getComputedStyle(sidebar).display === 'none';
        sidebarBtn.classList.toggle('active', isClosed);
    });
}

function bindMenuEvents() {
    const themeSelect = document.querySelector<HTMLDivElement>('#menu .theme-select')!;
    const themeMenuButton = menuSelect[2];

    menuBtn.addEventListener('click', event => {
        event.stopPropagation();
        const isOpen = window.getComputedStyle(menu).display === 'flex';
        menuBtn.classList.toggle('active', !isOpen);
    });

    themeMenuButton.addEventListener('click', () => {
        const isClosed = window.getComputedStyle(themeSelect).display === 'none';
        themeMenuButton.classList.toggle('active', isClosed);
    });

    menu.addEventListener('click', event => event.stopPropagation());

    document.addEventListener('click', () => {
        menuBtn.classList.remove('active');
        menuSelect.forEach(item => item.classList.remove('active'));
    });

    themeInput.forEach(radio => {
        radio.addEventListener('change', () => {
            if (!radio.checked) return;
            console.log('切换主题...');
            switchTheme(radio.value);
        });
    });
}

function bindSettingEvents() {
    const settingContainer = document.querySelector<HTMLDivElement>('#setting-container')!;
    const modelEditModal = document.querySelector<HTMLDivElement>('#model-edit-modal')!;
    const closeSettingButton = document.querySelector<HTMLButtonElement>('.close-setting')!;
    const openModifyButton = document.querySelector<HTMLButtonElement>('#open-modify-btn')!;
    const cancelModifyButton = document.querySelector<HTMLButtonElement>('#cancel-modify-btn')!;
    const modelSelect = document.querySelector<HTMLSelectElement>('#modelSelect')!;
    const modelNameSelect = document.querySelector<HTMLSelectElement>('#modelName')!;
    const modelSubmitButton = document.querySelector<HTMLButtonElement>('#submit-model-btn')!;
    const validLoader = document.querySelector<HTMLDivElement>('.valid-loader')!;
    const modalButtons = document.querySelector<HTMLDivElement>('.modal-btns')!;

    function updateDisplayInfo() {
        document.getElementById('display-merchant')!.innerText = modelConfig.model.model_merchant || '未配置';
        document.getElementById('display-model')!.innerText = modelConfig.model.model_name || '未配置';
        document.getElementById('display-key')!.innerText = '********';
    }

    function updateModelOptions() {
        const options = MODEL_OPTIONS[modelSelect.value] || MODEL_OPTIONS.deepseek;
        modelNameSelect.innerHTML = options.map(name => `<option value="${name}">${name}</option>`).join('');
    }

    menuSelect[1].addEventListener('click', event => {
        event.stopPropagation();
        openPopup(settingContainer);
        updateDisplayInfo();
    });

    closeSettingButton.addEventListener('click', async () => {
        if (await closePopup(settingContainer)) {
            modelEditModal.classList.remove('open', 'closing');
            modelEditModal.inert = false;
        }
    });

    openModifyButton.addEventListener('click', () => {
        openPopup(modelEditModal);
        modelSelect.value = modelConfig.model.model_merchant || 'deepseek';
        updateModelOptions();
        modelNameSelect.value = modelConfig.model.model_name || '';
    });

    cancelModifyButton.addEventListener('click', () => {
        void closePopup(modelEditModal);
    });

    modelSelect.addEventListener('change', updateModelOptions);

    modelSubmitButton.addEventListener('click', async () => {
        const modelData = {
            model_merchant: modelSelect.value,
            model_name: modelNameSelect.value
        };

        modelSubmitButton.disabled = true;
        modelSubmitButton.innerText = '验证中...';
        validLoader.classList.add('open');
        modalButtons.classList.add('close');

        try {
            const result = await modelAPI(modelData);
            if (result.status) {
                modelConfig.model = {...modelData};
                modelConfig.model_valid = true;
                saveModel(modelConfig);
                updateDisplayInfo();

                windowAlertP.innerText = '模型配置验证通过并已保存！';
                openPopup(windowAlertContainer);
                void closePopup(modelEditModal);
                document.querySelector<HTMLSpanElement>('#setting-container .setting-head span')!
                    .classList.remove('show-after');
            } else {
                windowAlertP.innerText = '验证失败: ' + (result.info || '原因未知');
                openPopup(windowAlertContainer);
            }
        } catch (error) {
            console.error('验证过程出错:', error);
            windowAlertP.innerText = '请求失败，请检查网络或后端服务';
            openPopup(windowAlertContainer);
        } finally {
            modelSubmitButton.disabled = false;
            modelSubmitButton.innerText = '确认修改';
            validLoader.classList.remove('open');
            modalButtons.classList.remove('close');
        }
    });
}

function bindAlertEvents() {
    const closeAlertButton = document.querySelector<HTMLButtonElement>(
        '.alert-window .alert-window-button button'
    )!;
    closeAlertButton.addEventListener('click', () => {
        void closePopup(windowAlertContainer);
    });
}

export function addEventToUi() {
    bindChatEvents();
    bindSidebarEvents();
    bindMenuEvents();
    bindSettingEvents();
    bindAlertEvents();
}
