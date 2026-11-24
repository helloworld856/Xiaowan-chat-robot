import {inputBox} from './ui.ts';

const composer = document.getElementById('chat-composer')!;
const toggle = document.getElementById('chat-input-toggle') as HTMLButtonElement;

function updateToggle() {
    if (!inputBox.value) composer.classList.remove('is-expanded');
    const expanded = composer.classList.contains('is-expanded');
    const label = expanded ? '收起输入框' : '展开输入框';
    toggle.hidden = !expanded && (!inputBox.value || inputBox.scrollHeight <= inputBox.clientHeight);
    toggle.setAttribute('aria-expanded', String(expanded));
    toggle.setAttribute('aria-label', label);
    toggle.title = label;
}

function setExpanded(expanded: boolean) {
    composer.classList.toggle('is-expanded', expanded);
    updateToggle();
    inputBox.focus({preventScroll: true});
}

export function initChatComposer() {
    inputBox.addEventListener('input', updateToggle);
    new ResizeObserver(updateToggle).observe(inputBox);
    toggle.addEventListener('pointerdown', event => {
        if (event.button === 0 && document.activeElement === inputBox) event.preventDefault();
    });
    toggle.addEventListener('click', () => setExpanded(!composer.classList.contains('is-expanded')));
    composer.addEventListener('keydown', event => {
        if (event.key !== 'Escape' || event.isComposing || event.keyCode === 229 || !composer.classList.contains('is-expanded')) return;
        event.preventDefault();
        event.stopPropagation();
        setExpanded(false);
    });
    updateToggle();
}

export function clearChatInput() {
    inputBox.value = '';
    updateToggle();
}
