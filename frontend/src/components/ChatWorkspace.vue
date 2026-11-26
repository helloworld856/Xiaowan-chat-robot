<script setup lang="ts">
import {computed, nextTick, onBeforeUnmount, onMounted, ref, watch} from 'vue';
import ChatComposer from './ChatComposer.vue';
import {useChat} from '../composables/useChat.ts';
import type {ChatTurn} from '../composables/useChat.ts';
import type {ProfileInfo} from '../ts/api.ts';
import {formatChatTimestamp, parseQuotedMessage, shouldShowTimeDivider} from '../ts/chat_format.ts';
import type {ConfirmOptions} from '../ts/dialogs.ts';
import type {PersonaInfo} from '../ts/global_config.ts';
import type {Preferences} from '../ts/preferences.ts';

const props = defineProps<{
    ready: boolean;
    shortcut: Preferences['send_shortcut'];
    profile: ProfileInfo | null;
    persona: PersonaInfo | null;
    clearingHistory: boolean;
    showAlert: (message: string) => void;
    confirmAction?: (options: ConfirmOptions) => Promise<string | null>;
}>();
const emit = defineEmits<{'busy-change': [busy: boolean]}>();

const chatBox = ref<HTMLDivElement | null>(null);
const composerDock = ref<HTMLDivElement | null>(null);
const composer = ref<InstanceType<typeof ChatComposer> | null>(null);
const reviewToggle = ref<HTMLButtonElement | null>(null);
const reviewSearch = ref<HTMLInputElement | null>(null);
const reviewList = ref<HTMLDivElement | null>(null);
const reviewOpen = ref(false);
const search = ref('');
const selectedBubble = ref('');
const activeBubble = ref('');
const copiedBubble = ref('');
const showBottomButton = ref(false);
const viewRevision = ref(0);
const composerRevision = ref(0);
let highlightTimer: ReturnType<typeof setTimeout> | undefined;
let copiedTimer: ReturnType<typeof setTimeout> | undefined;
let observer: ResizeObserver | undefined;
let scrollingToBottom = false;

const chatState = useChat({
    canSend: () => props.ready && !props.clearingHistory,
    showAlert: message => props.showAlert(message),
    onBusyChange: value => emit('busy-change', value),
    scrollAfterRender,
});
const {
    entries, busy, isTyping, draft, sendMessage, activateFailure, loadHistory,
    clearHistory: clearEntries, reset: resetChat,
} = chatState;
const quote = chatState.quote ?? ref(null);
const cancellable = chatState.cancellable ?? ref(false);
const typingStatus = chatState.typingStatus ?? ref('');

const locked = computed(() => busy.value || !props.ready || props.clearingHistory);
const userAvatar = computed(() => props.profile?.avatar_url || props.persona?.USER_AVATAR || undefined);
const userName = computed(() => props.profile?.nickname || '你');
const botName = computed(() => props.persona?.BOT_NAME || '小晚');
const botAvatar = computed(() => props.persona?.BOT_AVATAR || undefined);
const query = computed(() => search.value.trim().toLocaleLowerCase());

function userBubbleId(id: string) { return `chat-user-${id}`; }
function replyBubbleId(id: string, index: number) { return `chat-reply-${id}-${index}`; }

const latestTurnId = computed(() => {
    const last = entries.value[entries.value.length - 1];
    return last?.kind === 'turn' ? last.id : '';
});

const timeline = computed(() => {
    let previousAt: number | null | undefined;
    return entries.value.map(entry => {
        if (entry.kind !== 'turn') {
            return {entry, divider: null as string | null, parsedUser: null};
        }
        const showDivider = shouldShowTimeDivider(entry.createdAt, previousAt);
        const divider = showDivider && typeof entry.createdAt === 'number'
            ? formatChatTimestamp(entry.createdAt)
            : null;
        if (typeof entry.createdAt === 'number' && Number.isFinite(entry.createdAt)) {
            previousAt = entry.createdAt;
        }
        return {
            entry,
            divider,
            parsedUser: parseQuotedMessage(entry.user),
        };
    });
});

const matches = computed(() => {
    const results = [];
    const needle = query.value;
    for (let index = entries.value.length - 1; index >= 0; index--) {
        const entry = entries.value[index];
        if (entry.kind !== 'turn') continue;
        const userBody = parseQuotedMessage(entry.user).body;
        const userMatches = userBody.toLocaleLowerCase().includes(needle) || entry.user.toLocaleLowerCase().includes(needle);
        const replyIndex = needle ? entry.replies.findIndex(reply => reply.toLocaleLowerCase().includes(needle)) : -1;
        if (!userMatches && replyIndex < 0) continue;
        results.push({
            id: entry.id,
            user: userBody,
            reply: entry.replies[replyIndex < 0 ? 0 : replyIndex],
            target: userMatches ? userBubbleId(entry.id) : replyBubbleId(entry.id, replyIndex)
        });
    }
    return results;
});

watch(matches, () => {
    const list = reviewList.value;
    const active = document.activeElement;
    if (!reviewOpen.value || !list?.contains(active)) return;
    const buttons = Array.from(list.querySelectorAll<HTMLButtonElement>('.review-item'));
    const index = buttons.findIndex(button => button === active);
    const focused = buttons[index];
    if (!focused) return;
    void nextTick(() => {
        if (!reviewOpen.value || reviewList.value !== list || document.activeElement !== document.body) return;
        const remaining = list.querySelectorAll<HTMLButtonElement>('.review-item');
        const target = list.contains(focused) ? focused
            : remaining[Math.min(index, remaining.length - 1)] ?? reviewSearch.value;
        target?.focus({preventScroll: true});
    });
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

function clearCopiedState() {
    clearTimeout(copiedTimer);
    copiedTimer = undefined;
    copiedBubble.value = '';
}

function toggleBubbleActions(bubbleId: string) {
    const selection = typeof globalThis.getSelection === 'function' ? globalThis.getSelection()?.toString() : '';
    if (selection) return;
    activeBubble.value = activeBubble.value === bubbleId ? '' : bubbleId;
}

function onChatBackgroundClick(event: MouseEvent) {
    if (event.target instanceof Element && event.target.closest('.bubble-wrap')) return;
    activeBubble.value = '';
}

async function copyBubbleText(bubbleId: string, text: string) {
    let copied = false;
    try {
        if (globalThis.navigator?.clipboard?.writeText) {
            await globalThis.navigator.clipboard.writeText(text);
            copied = true;
        }
    } catch {
        copied = false;
    }
    if (!copied && typeof document !== 'undefined' && typeof document.createElement === 'function') {
        try {
            const area = document.createElement('textarea');
            area.value = text;
            area.setAttribute('readonly', '');
            area.style.position = 'fixed';
            area.style.opacity = '0';
            document.body.appendChild(area);
            area.select();
            copied = document.execCommand('copy');
            area.remove();
        } catch {
            copied = false;
        }
    }
    if (!copied) {
        props.showAlert('复制失败，请手动选中文字复制。');
        return;
    }
    clearCopiedState();
    copiedBubble.value = bubbleId;
    copiedTimer = setTimeout(clearCopiedState, 1400);
}

function quoteBubble(speaker: string, text: string) {
    chatState.setQuote?.(speaker, text);
    activeBubble.value = '';
    void nextTick(() => composer.value?.focus?.());
}

async function requestRegenerate(turn: ChatTurn) {
    activeBubble.value = '';
    await chatState.regenerateTurn?.(turn);
}

async function requestDeleteTurn(turn: ChatTurn) {
    if (locked.value) return;
    if (props.confirmAction) {
        const confirmed = await props.confirmAction({
            title: '确认删除这轮对话？',
            description: '删除后这轮问答将从聊天记录中移除，且无法恢复。',
            action: '删除对话',
            destructive: true,
        });
        if (confirmed === null) return;
    }
    activeBubble.value = '';
    await chatState.deleteTurn?.(turn);
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
    if (event.defaultPrevented || event.key !== 'Escape' || event.isComposing || event.keyCode === 229) return;
    if (event.target instanceof Element && event.target.closest('dialog[open]')) return;
    if (activeBubble.value) {
        activeBubble.value = '';
        return;
    }
    if (!reviewOpen.value) return;
    closeReview();
}

function resetReview() {
    viewRevision.value++;
    search.value = '';
    activeBubble.value = '';
    clearHighlight();
    clearCopiedState();
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
    const box = chatBox.value;
    const dock = composerDock.value;
    if (box && dock) {
        observer = new ResizeObserver(changes => {
            const dockSize = changes.find(change => change.target === dock)?.borderBoxSize[0];
            if (dockSize && dockSize.blockSize > 0) {
                // 输入栏向上展开时为末条消息留出空间，翻阅历史时保留阅读位置。
                const follow = isNearBottom(2) || scrollingToBottom;
                box.style.setProperty('--composer-height', `${dockSize.blockSize}px`);
                if (follow) box.scrollTo({top: box.scrollHeight, behavior: 'instant'});
            }
            updateBottomButton();
        });
        observer.observe(box, {box: 'border-box'});
        observer.observe(dock, {box: 'border-box'});
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
                <input :key="viewRevision" id="review-search" ref="reviewSearch" v-model="search" type="search" aria-label="搜索聊天内容"
                    placeholder="输入关键词" autocomplete="off">
                <p id="review-count" role="status">{{ query ? `${matches.length} 条匹配记录` : `${matches.length} 条聊天记录` }}</p>
                <div id="review-list" ref="reviewList">
                    <button v-for="match in matches" :key="match.id" type="button" class="review-item"
                        @click="jumpToBubble(match.target)">
                        <span class="review-item-user">{{ match.user }}</span>
                        <span v-if="match.reply !== undefined" class="review-item-reply">{{ match.reply }}</span>
                    </button>
                </div>
            </div>
        </aside>
    </div>

    <main class="chat-layout" aria-label="聊天">
        <div id="chat-container">
            <div id="chatBox" ref="chatBox" @scroll.passive="updateBottomButton" @wheel.passive="stopAutoScroll"
                @pointerdown="stopAutoScroll" @keydown="stopAutoScroll" @click="onChatBackgroundClick">
                <template v-for="{entry, divider, parsedUser} in timeline" :key="entry.id">
                    <template v-if="entry.kind === 'turn'">
                        <div v-if="divider" class="chat-time-divider" role="separator" :aria-label="`对话时间 ${divider}`">
                            <span>{{ divider }}</span>
                        </div>
                        <div class="user_message" :data-conversation-round="entry.round">
                            <button v-if="entry.failure" class="chat-send-failed" type="button"
                                :aria-label="entry.failure.retryable ? '发送失败，重试发送' : '发送失败，查看原因'"
                                @click="activateFailure(entry)">!</button>
                            <div class="bubble-wrap user-bubble-wrap"
                                :class="{'actions-visible': activeBubble === userBubbleId(entry.id)}">
                                <div :id="userBubbleId(entry.id)" class="user_bubble"
                                    :class="{'review-selected': selectedBubble === userBubbleId(entry.id)}"
                                    @click="toggleBubbleActions(userBubbleId(entry.id))">
                                    <div v-if="parsedUser?.quote" class="bubble-quote">
                                        <span class="bubble-quote-speaker">{{ parsedUser.quote.speaker }}：</span>{{ parsedUser.quote.text }}
                                    </div>
                                    <span>{{ parsedUser ? parsedUser.body : entry.user }}</span>
                                </div>
                                <div class="bubble-actions user-bubble-actions" role="toolbar" aria-label="消息操作">
                                    <button type="button" class="bubble-action-btn"
                                        @click.stop="copyBubbleText(userBubbleId(entry.id), parsedUser ? parsedUser.body : entry.user)">
                                        {{ copiedBubble === userBubbleId(entry.id) ? '已复制' : '复制' }}
                                    </button>
                                    <button type="button" class="bubble-action-btn" :disabled="locked"
                                        @click.stop="quoteBubble(userName, parsedUser ? parsedUser.body : entry.user)">引用</button>
                                    <button v-if="entry.id === latestTurnId && !entry.failure" type="button"
                                        class="bubble-action-btn" :disabled="locked"
                                        @click.stop="requestRegenerate(entry)">重生成</button>
                                    <button type="button" class="bubble-action-btn bubble-action-danger" :disabled="locked"
                                        @click.stop="requestDeleteTurn(entry)">删除</button>
                                </div>
                            </div>
                            <div class="user_triangle"></div>
                            <div class="user_avatar" :title="userName"><img :src="userAvatar" alt="你的头像"></div>
                        </div>
                        <div v-for="(reply, index) in entry.replies" :key="index" class="assistant_message">
                            <div class="assistant_bag">
                                <div class="assistant_avatar"><img :src="botAvatar" alt="对方的头像"></div>
                                <div class="assistant_triangle"></div>
                                <div class="bubble-wrap assistant-bubble-wrap"
                                    :class="{'actions-visible': activeBubble === replyBubbleId(entry.id, index)}">
                                    <div :id="replyBubbleId(entry.id, index)" class="assistant_bubble"
                                        :class="{'review-selected': selectedBubble === replyBubbleId(entry.id, index)}"
                                        @click="toggleBubbleActions(replyBubbleId(entry.id, index))">{{ reply }}</div>
                                    <div class="bubble-actions assistant-bubble-actions" role="toolbar" aria-label="回复操作">
                                        <button type="button" class="bubble-action-btn"
                                            @click.stop="copyBubbleText(replyBubbleId(entry.id, index), reply)">
                                            {{ copiedBubble === replyBubbleId(entry.id, index) ? '已复制' : '复制' }}
                                        </button>
                                        <button type="button" class="bubble-action-btn" :disabled="locked"
                                            @click.stop="quoteBubble(botName, reply)">引用</button>
                                        <button v-if="entry.id === latestTurnId && index === entry.replies.length - 1"
                                            type="button" class="bubble-action-btn" :disabled="locked"
                                            @click.stop="requestRegenerate(entry)">重生成</button>
                                        <button v-if="index === entry.replies.length - 1" type="button"
                                            class="bubble-action-btn bubble-action-danger" :disabled="locked"
                                            @click.stop="requestDeleteTurn(entry)">删除</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </template>
                    <div v-else class="user_message">
                        <button type="button" class="chat-delivery-action"
                            @click="showAlert(`${entry.message}\n\n原消息：\n${entry.user}`)">查看发送结果</button>
                    </div>
                </template>
                <div v-if="isTyping" id="chat-typing" class="assistant_message" role="status"
                    :aria-label="typingStatus || '对方正在输入'">
                    <div class="assistant_bag">
                        <div class="assistant_avatar"><img :src="botAvatar" alt=""></div>
                        <div class="assistant_triangle"></div>
                        <div class="assistant_bubble chat-typing-dots">
                            <span class="typing-dot" aria-hidden="true"></span>
                            <span class="typing-dot" aria-hidden="true"></span>
                            <span class="typing-dot" aria-hidden="true"></span>
                            <em v-if="typingStatus" class="chat-typing-status">{{ typingStatus }}</em>
                        </div>
                    </div>
                </div>
            </div>
            <div ref="composerDock" class="chat-dock">
                <button id="comeToBottom" type="button" title="回到底部" aria-label="回到底部"
                    :style="{display: showBottomButton ? 'block' : ''}" @click="scrollToBottom">
                    <img src="/ico/arrow.png" alt="">
                </button>
                <ChatComposer :key="composerRevision" ref="composer" v-model="draft" :shortcut="shortcut"
                    :locked="locked" :cancellable="cancellable" :quote="quote"
                    @send="sendMessage" @cancel="chatState.cancelSend?.()" @clear-quote="chatState.clearQuote?.()" />
            </div>
        </div>
    </main>
</template>
