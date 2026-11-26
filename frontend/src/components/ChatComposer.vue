<script setup lang="ts">
import {computed, nextTick, onBeforeUnmount, onMounted, ref, watch} from 'vue';
import {shouldSendMessage} from '../ts/preferences.ts';
import type {Preferences} from '../ts/preferences.ts';

const props = withDefaults(defineProps<{
    locked: boolean;
    cancellable?: boolean;
    quote?: {speaker: string; text: string} | null;
    shortcut: Preferences['send_shortcut'];
}>(), {
    cancellable: false,
    quote: null,
});
const emit = defineEmits<{send: []; cancel: []; 'clear-quote': []}>();
const draft = defineModel<string>({required: true});

const input = ref<HTMLTextAreaElement | null>(null);
const expanded = ref(false);
const overflow = ref(false);
const composing = ref(false);
const toggleLabel = computed(() => expanded.value ? '收起输入框' : '展开输入框');
let observer: ResizeObserver | undefined;

function measureOverflow() {
    const element = input.value;
    overflow.value = Boolean(element && element.value && element.scrollHeight > element.clientHeight);
}

watch(draft, value => {
    if (!value) expanded.value = false;
    void nextTick(measureOverflow);
});

function focus() {
    input.value?.focus({preventScroll: true});
}

function setExpanded(value: boolean) {
    expanded.value = value;
    focus();
    void nextTick(measureOverflow);
}

function preserveInputFocus(event: PointerEvent) {
    if (event.button === 0 && document.activeElement === input.value) event.preventDefault();
}

function send() {
    if (!composing.value && !props.locked) emit('send');
}

function onPrimaryAction() {
    if (props.cancellable) emit('cancel');
    else send();
}

function onInputKey(event: KeyboardEvent) {
    if (composing.value || !shouldSendMessage(event, props.shortcut)) return;
    event.preventDefault();
    send();
}

function onEscape(event: KeyboardEvent) {
    if (composing.value || event.isComposing || event.keyCode === 229) {
        event.stopPropagation();
        return;
    }
    if (expanded.value) {
        event.preventDefault();
        event.stopPropagation();
        setExpanded(false);
        return;
    }
    if (props.quote) {
        event.preventDefault();
        event.stopPropagation();
        emit('clear-quote');
    }
}

onMounted(() => {
    if (input.value) {
        observer = new ResizeObserver(measureOverflow);
        observer.observe(input.value);
    }
    measureOverflow();
});
onBeforeUnmount(() => observer?.disconnect());
defineExpose({focus});
</script>

<template>
    <div v-if="quote" id="chat-quote-bar" role="region" aria-label="引用消息">
        <span class="chat-quote-text" :title="`${quote.speaker}：${quote.text}`">
            <strong>引用 {{ quote.speaker }}：</strong>{{ quote.text }}
        </span>
        <button id="chat-quote-clear" type="button" aria-label="取消引用" title="取消引用"
            @pointerdown="preserveInputFocus" @click="emit('clear-quote')">×</button>
    </div>
    <div id="inputArea">
        <div id="chat-composer" :class="{'is-expanded': expanded}" @keydown.esc="onEscape">
            <textarea id="userInput" ref="input" v-model="draft" autocomplete="off" rows="1"
                aria-label="聊天消息" placeholder="说点什么吧~" @keydown="onInputKey"
                @compositionstart="composing = true" @compositionend="composing = false"
                @change="composing = false" @input="composing && measureOverflow()"></textarea>
            <button id="chat-input-toggle" type="button" :aria-label="toggleLabel" aria-controls="userInput"
                :aria-expanded="expanded" :title="toggleLabel" :hidden="!expanded && !overflow"
                @pointerdown="preserveInputFocus" @click="setExpanded(!expanded)"></button>
        </div>
        <button id="chat-submit-btn" class="sendbtn" :class="{'is-stop': cancellable}" type="button"
            :disabled="locked && !cancellable" :aria-label="cancellable ? '停止生成' : undefined"
            @click="onPrimaryAction">
            {{ cancellable ? '停止' : locked ? '......' : '发送' }}
        </button>
    </div>
</template>
