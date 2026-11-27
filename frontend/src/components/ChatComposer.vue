<script setup lang="ts">
import {computed, nextTick, onBeforeUnmount, onMounted, ref, watch} from 'vue';
import {shouldSendMessage} from '../ts/preferences.ts';
import type {Preferences} from '../ts/preferences.ts';

const props = defineProps<{locked: boolean; shortcut: Preferences['send_shortcut']}>();
const emit = defineEmits<{send: []}>();
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

function setExpanded(value: boolean) {
    expanded.value = value;
    input.value?.focus({preventScroll: true});
    void nextTick(measureOverflow);
}

function preserveInputFocus(event: PointerEvent) {
    if (event.button === 0 && document.activeElement === input.value) event.preventDefault();
}

function send() {
    if (!composing.value && !props.locked) emit('send');
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
    if (!expanded.value) return;
    event.preventDefault();
    event.stopPropagation();
    setExpanded(false);
}

onMounted(() => {
    if (input.value) {
        observer = new ResizeObserver(measureOverflow);
        observer.observe(input.value);
    }
    measureOverflow();
});
onBeforeUnmount(() => observer?.disconnect());
</script>

<template>
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
        <button class="sendbtn" type="button" :disabled="locked" @click="send">{{ locked ? '......' : '发送' }}</button>
    </div>
</template>
