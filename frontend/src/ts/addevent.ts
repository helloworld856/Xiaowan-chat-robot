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
import {initChatReview} from './review.ts';
import {preferences, shouldSendMessage} from './preferences.ts';
import {initSettings} from './settings.ts';

function bindChatEvents() {
    sendBtn.onclick = () => sendMessage();

    inputBox.addEventListener('keydown', event => {
        if (shouldSendMessage(event, preferences.send_shortcut)) {
            event.preventDefault();
            if (!sendBtn.disabled) void sendMessage();
        }
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
        sidebarBtn.setAttribute('aria-expanded', String(isClosed));
        sidebar.inert = !isClosed;
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
    initChatReview();
    bindMenuEvents();
    initSettings();
    bindAlertEvents();
}
