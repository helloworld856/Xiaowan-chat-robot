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
        // 把一条用户消息后的多条回复归为同一轮，跳过输入中占位。
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
            const preview = document.createElement('span');
            preview.className = 'review-item-user';
            preview.textContent = userText;
            button.appendChild(preview);
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

        count.textContent = query ? `找到 ${matches} 条匹配记录` : '';
        if (!matches) {
            const empty = document.createElement('p');
            empty.className = 'review-empty';
            empty.textContent = query ? '没有找到匹配的聊天记录。' : '暂无聊天记录。';
            fragment.appendChild(empty);
        }
        // 重建列表时保留键盘焦点位置，避免气泡追加后焦点丢失。
        const focusedIndex = Array.from(list.children).indexOf(document.activeElement as Element);
        list.replaceChildren(fragment);
        if (focusedIndex >= 0) (list.children[focusedIndex] as HTMLElement | undefined)?.focus({preventScroll: true});
    }

    search.addEventListener('input', render);
    new MutationObserver(render).observe(chatBox, {childList: true});
    render();
}
