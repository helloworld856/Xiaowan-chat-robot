import {chatBox, sidebar, sidebarBtn} from './ui.ts';

interface ReviewEntry {
    user: HTMLElement;
    replies: HTMLElement[];
}

// 从当前页面建立索引，不另存聊天，也不改变后端历史的生命周期。
export function initChatReview() {
    const search = document.getElementById('review-search') as HTMLInputElement;
    const list = document.getElementById('review-list')!;
    const count = document.getElementById('review-count')!;
    let selected: HTMLElement | undefined;
    let highlightTimer: ReturnType<typeof setTimeout> | undefined;

    function closeReview() {
        sidebarBtn.classList.remove('active');
        sidebarBtn.setAttribute('aria-expanded', 'false');
        sidebar.inert = true;
        sidebarBtn.focus();
    }

    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && sidebarBtn.classList.contains('active')) closeReview();
    });

    function render() {
        const entries: ReviewEntry[] = [];
        for (const row of chatBox.children) {
            const user = row.querySelector<HTMLElement>('.user_bubble');
            if (user) {
                entries.push({user, replies: []});
            } else if (row.id !== 'chat-typing') {
                const reply = row.querySelector<HTMLElement>('.assistant_bubble');
                if (reply && entries.length) entries[entries.length - 1]!.replies.push(reply);
            }
        }

        const query = search.value.trim().toLocaleLowerCase();
        const fragment = document.createDocumentFragment();
        let matches = 0;
        for (let index = entries.length - 1; index >= 0; index--) {
            const entry = entries[index]!;
            const userText = entry.user.textContent || '';
            const matchingReply = entry.replies.find(reply =>
                (reply.textContent || '').toLocaleLowerCase().includes(query));
            const userMatches = userText.toLocaleLowerCase().includes(query);
            if (query && !userMatches && !matchingReply) continue;
            matches++;

            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'review-item';
            const number = document.createElement('span');
            number.className = 'review-item-number';
            number.textContent = `对话 ${index + 1}`;
            const preview = document.createElement('span');
            preview.className = 'review-item-user';
            preview.textContent = userText;
            button.append(number, preview);
            const reply = matchingReply || entry.replies[0];
            if (reply) {
                const replyPreview = document.createElement('span');
                replyPreview.className = 'review-item-reply';
                replyPreview.textContent = reply.textContent;
                button.appendChild(replyPreview);
            }
            button.addEventListener('click', () => {
                closeReview();
                if (highlightTimer !== undefined) clearTimeout(highlightTimer);
                selected?.classList.remove('review-selected');
                selected = query && !userMatches && matchingReply ? matchingReply : entry.user;
                selected.scrollIntoView({block: 'center', behavior:
                    matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
                selected.classList.add('review-selected');
                highlightTimer = setTimeout(() => selected?.classList.remove('review-selected'), 2000);
            });
            fragment.appendChild(button);
        }

        count.textContent = query ? `找到 ${matches} 条对话` : `共 ${entries.length} 条对话 · 最近在前`;
        if (!matches) {
            const empty = document.createElement('p');
            empty.className = 'review-empty';
            empty.textContent = query ? '没有找到相关对话，试试其他关键词。' : '还没有对话，聊几句后再来看看吧。';
            fragment.appendChild(empty);
        }
        const focusedIndex = Array.from(list.children).indexOf(document.activeElement as Element);
        list.replaceChildren(fragment);
        if (focusedIndex >= 0) (list.children[focusedIndex] as HTMLElement | undefined)?.focus({preventScroll: true});
    }

    search.addEventListener('input', render);
    new MutationObserver(render).observe(chatBox, {childList: true});
    render();
}
