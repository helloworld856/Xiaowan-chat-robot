// 给组件绑定事件
import {
    sendBtn,
    inputBox,
    comeToBottom,
    sidebarBtn,
    sidebar,
    menuBtn,
    menu,
    windowAlertContainer,
    chatBox,
    closeMenu,
    closePopup
} from './ui.ts';
import {sendMessage} from './chat.ts';
import {initChatComposer} from './composer.ts';
import {initChatReview} from './review.ts';
import {preferences, shouldSendMessage} from './preferences.ts';
import {initSettings} from './settings.ts';
import {initProfile} from './profile.ts';

function bindChatEvents() {
    initChatComposer();
    sendBtn.onclick = () => sendMessage();

    inputBox.addEventListener('keydown', event => {
        if (shouldSendMessage(event, preferences.send_shortcut)) {
            event.preventDefault();
            if (!sendBtn.disabled) void sendMessage();
        }
    });

    chatBox.addEventListener('scroll', () => {
        // 离底部较远时才显示返回按钮，避免轻微滚动时闪烁。
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
        // 侧栏收起时同时移出键盘交互范围。
        sidebar.inert = !isClosed;
    });
}

function bindMenuEvents() {
    menuBtn.addEventListener('click', event => {
        event.stopPropagation();
        if (menuBtn.classList.contains('active')) closeMenu();
        else {
            menuBtn.classList.add('active');
            menuBtn.setAttribute('aria-expanded', 'true');
        }
    });

    menu.addEventListener('click', event => event.stopPropagation());

    document.addEventListener('click', closeMenu);
    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape' || !menuBtn.classList.contains('active')) return;
        if (document.activeElement === menuBtn || menu.contains(document.activeElement)) {
            closeMenu();
            menuBtn.focus();
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
    windowAlertContainer.addEventListener('cancel', event => {
        event.preventDefault();
        void closePopup(windowAlertContainer);
    });
}

export function addEventToUi() {
    bindChatEvents();
    bindSidebarEvents();
    initChatReview();
    bindMenuEvents();
    initSettings();
    initProfile();
    bindAlertEvents();
}
