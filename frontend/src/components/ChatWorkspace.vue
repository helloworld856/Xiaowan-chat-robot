<script setup lang="ts">
import {computed, nextTick, onBeforeUnmount, onMounted, ref} from 'vue';
import ChatComposer from './ChatComposer.vue';
import {useChat} from '../composables/useChat.ts';
import type {ProfileInfo} from '../ts/api.ts';
import type {PersonaInfo} from '../ts/global_config.ts';
import type {Preferences} from '../ts/preferences.ts';

const props = defineProps<{
    ready: boolean;
    shortcut: Preferences['send_shortcut'];
    profile: ProfileInfo | null;
    persona: PersonaInfo | null;
    clearingHistory: boolean;
    showAlert: (message: string) => void;
}>();
const emit = defineEmits<{'busy-change': [busy: boolean]}>();

const chatBox = ref<HTMLDivElement | null>(null);
const reviewToggle = ref<HTMLButtonElement | null>(null);
const reviewOpen = ref(false);
const search = ref('');
const selectedBubble = ref('');
const showBottomButton = ref(false);
const viewRevision = ref(0);
const composerRevision = ref(0);
let highlightTimer: ReturnType<typeof setTimeout> | undefined;
let observer: ResizeObserver | undefined;
let scrollingToBottom = false;

const {
    entries, busy, isTyping, draft, sendMessage, activateFailure, loadHistory,
    clearHistory: clearEntries, reset: resetChat
} = useChat({
    canSend: () => props.ready && !props.clearingHistory,
    showAlert: message => props.showAlert(message),
    onBusyChange: value => emit('busy-change', value),
    scrollAfterRender
});

const locked = computed(() => busy.value || !props.ready || props.clearingHistory);
const userAvatar = computed(() => props.profile?.avatar_url || props.persona?.USER_AVATAR || undefined);
const userName = computed(() => props.profile?.nickname || '你');
const botAvatar = computed(() => props.persona?.BOT_AVATAR || undefined);
const query = computed(() => search.value.trim().toLocaleLowerCase());

function userBubbleId(id: string) { return `chat-user-${id}`; }
function replyBubbleId(id: string, index: number) { return `chat-reply-${id}-${index}`; }

const matches = computed(() => {
    const results = [];
    const needle = query.value;
    for (let index = entries.value.length - 1; index >= 0; index--) {
        const entry = entries.value[index];
        if (entry.kind !== 'turn') continue;
        const userMatches = entry.user.toLocaleLowerCase().includes(needle);
        const replyIndex = needle ? entry.replies.findIndex(reply => reply.toLocaleLowerCase().includes(needle)) : -1;
        if (!userMatches && replyIndex < 0) continue;
        results.push({
            id: entry.id,
            user: entry.user,
            reply: entry.replies[replyIndex < 0 ? 0 : replyIndex],
            target: userMatches ? userBubbleId(entry.id) : replyBubbleId(entry.id, replyIndex)
        });
    }
    return results;
});

function isNearBottom(distance: number) {
    const box = chatBox.value;
    return !box || box.scrollTop + box.clientHeight >= box.scrollHeight - distance;
}

function updateBottomButton() {
    if (isNearBottom(2)) scrollingToBottom = false;
    showBottomButton.value = !isNearBottom(120);
}

function stopAutoScroll() {
    scrollingToBottom = false;
}

function scrollToBottom() {
    const box = chatBox.value;
    if (!box) return;
    scrollingToBottom = !isNearBottom(2);
    box.scrollTo({top: box.scrollHeight, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
    showBottomButton.value = false;
}

function scrollAfterRender(forceBottom: boolean) {
    // 在 Vue 更新气泡前记录用户的位置，浏览旧消息时不跟随新回复。
    const follow = forceBottom || scrollingToBottom || isNearBottom(2);
    const revision = viewRevision.value;
    void nextTick(() => {
        if (revision !== viewRevision.value) return;
        if (follow) scrollToBottom();
        else updateBottomButton();
    });
}

function closeReview() {
    reviewOpen.value = false;
    reviewToggle.value?.focus({preventScroll: true});
}

function clearHighlight() {
    clearTimeout(highlightTimer);
    highlightTimer = undefined;
    selectedBubble.value = '';
}

function jumpToBubble(target: string) {
    closeReview();
    clearHighlight();
    stopAutoScroll();
    const bubble = chatBox.value?.querySelector<HTMLElement>(`#${target}`);
    if (!bubble) return;
    selectedBubble.value = target;
    bubble.scrollIntoView({
        block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'
    });
    highlightTimer = setTimeout(clearHighlight, 2000);
}

function onEscape(event: KeyboardEvent) {
    if (!event.defaultPrevented && event.key === 'Escape' && !event.isComposing && event.keyCode !== 229 && reviewOpen.value) {
        closeReview();
    }
}

function resetReview() {
    viewRevision.value++;
    search.value = '';
    clearHighlight();
    stopAutoScroll();
    showBottomButton.value = false;
}

function clearHistory() {
    clearEntries();
    resetReview();
}

function reset() {
    resetChat();
    resetReview();
    reviewOpen.value = false;
    // 重建输入框，丢弃尚未写入草稿的输入法文本。
    composerRevision.value++;
}

onMounted(() => {
    window.addEventListener('keydown', onEscape);
    if (chatBox.value) {
        observer = new ResizeObserver(updateBottomButton);
        observer.observe(chatBox.value);
    }
});
onBeforeUnmount(() => {
    window.removeEventListener('keydown', onEscape);
    observer?.disconnect();
    reset();
});

defineExpose({loadHistory, clearHistory, reset});
</script>

<template>
    <div id="sidebar-zone">
        <button ref="reviewToggle" class="sidebar-btn" :class="{active: reviewOpen}" type="button"
            title="查找聊天记录" aria-label="查找聊天记录" :aria-expanded="reviewOpen" aria-controls="chat-review"
            @click="reviewOpen = !reviewOpen">
            <svg class="arrow" viewBox="0 0 512 512" aria-hidden="true">
                <path d="M233.4 105.4c12.5-12.5 32.8-12.5 45.3 0l192 192c12.5 12.5 12.5 32.8 0 45.3s-32.8 12.5-45.3 0L256 173.3 86.6 342.6c-12.5 12.5-32.8 12.5-45.3 0s-12.5-32.8 0-45.3l192-192z"></path>
            </svg>
        </button>
        <aside id="chat-review" class="sidebar" aria-labelledby="review-title" :inert="!reviewOpen">
            <div class="review-layout">
                <div class="review-header">
                    <h2 id="review-title">查找聊天记录</h2>
                </div>
                <input :key="viewRevision" id="review-search" v-model="search" type="search" aria-label="搜索聊天内容"
                    placeholder="输入关键词" autocomplete="off">
                <p id="review-count" role="status">{{ query ? `找到 ${matches.length} 条匹配记录` : '' }}</p>
                <div id="review-list">
                    <button v-for="match in matches" :key="match.id" type="button" class="review-item"
                        @click="jumpToBubble(match.target)">
                        <span class="review-item-user">{{ match.user }}</span>
                        <span v-if="match.reply !== undefined" class="review-item-reply">{{ match.reply }}</span>
                    </button>
                    <p v-if="!matches.length" class="review-empty">{{ query ? '没有找到匹配的聊天记录。' : '暂无聊天记录。' }}</p>
                </div>
            </div>
        </aside>
    </div>

    <main class="chat-layout" aria-label="聊天">
        <div id="chat-container">
            <div id="chatBox" ref="chatBox" @scroll.passive="updateBottomButton" @wheel.passive="stopAutoScroll"
                @pointerdown="stopAutoScroll" @keydown="stopAutoScroll">
                <template v-for="entry in entries" :key="entry.id">
                    <template v-if="entry.kind === 'turn'">
                        <div class="user_message" :data-conversation-round="entry.round">
                            <button v-if="entry.failure" class="chat-send-failed" type="button" title="发送失败"
                                :aria-label="entry.failure.retryable ? '发送失败，重试发送' : '发送失败，查看原因'"
                                @click="activateFailure(entry)">!</button>
                            <div :id="userBubbleId(entry.id)" class="user_bubble"
                                :class="{'review-selected': selectedBubble === userBubbleId(entry.id)}">{{ entry.user }}</div>
                            <div class="user_triangle"></div>
                            <div class="user_avatar" :title="userName"><img :src="userAvatar" alt="你的头像"></div>
                        </div>
                        <div v-for="(reply, index) in entry.replies" :key="index" class="assistant_message">
                            <div class="assistant_bag">
                                <div class="assistant_avatar"><img :src="botAvatar" alt="对方的头像"></div>
                                <div class="assistant_triangle"></div>
                                <div :id="replyBubbleId(entry.id, index)" class="assistant_bubble"
                                    :class="{'review-selected': selectedBubble === replyBubbleId(entry.id, index)}">{{ reply }}</div>
                            </div>
                        </div>
                    </template>
                    <details v-else class="chat-delivery chat-delivery-uncertain">
                        <summary><span role="status">{{ entry.message }}</span></summary>
                        <p>{{ entry.user }}</p>
                    </details>
                </template>
                <div v-if="isTyping" id="chat-typing" class="assistant_message" role="status" aria-label="对方正在输入">
                    <div class="assistant_bag">
                        <div class="assistant_avatar"><img :src="botAvatar" alt=""></div>
                        <div class="assistant_triangle"></div>
                        <div class="assistant_bubble chat-typing-dots" aria-hidden="true"><span></span><span></span><span></span></div>
                    </div>
                </div>
            </div>
            <button id="comeToBottom" type="button" title="回到底部" aria-label="回到底部"
                :style="{display: showBottomButton ? 'block' : ''}" @click="scrollToBottom">
                <img src="/ico/arrow.png" alt="">
            </button>
        </div>
        <ChatComposer :key="composerRevision" v-model="draft" :shortcut="shortcut" :locked="locked" @send="sendMessage" />
    </main>
</template>
