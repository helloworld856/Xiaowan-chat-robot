<script setup lang="ts">
import {computed, nextTick, onBeforeUnmount, onMounted, ref, watch} from 'vue';
import type {ChatAttachment} from '../ts/global_config.ts';
import {shouldSendMessage} from '../ts/preferences.ts';
import type {Preferences} from '../ts/preferences.ts';

function formatFileSize(bytes: number): string {
    if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10240 ? 1 : 0)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const ACCEPTED_FILES = [
    'image/png', 'image/jpeg', 'image/webp', 'image/gif',
    '.pdf', '.txt', '.md', '.markdown', '.csv', '.tsv', '.json', '.yaml', '.yml', '.log',
    '.py', '.js', '.ts', '.tsx', '.jsx', '.vue', '.html', '.css', '.sql', '.xml', '.ini', '.toml',
].join(',');

const props = withDefaults(defineProps<{
    locked: boolean;
    cancellable?: boolean;
    uploading?: boolean;
    attachments?: ChatAttachment[];
    quote?: {speaker: string; text: string} | null;
    shortcut: Preferences['send_shortcut'];
}>(), {
    cancellable: false,
    uploading: false,
    attachments: () => [],
    quote: null,
});
const emit = defineEmits<{
    send: [];
    cancel: [];
    'clear-quote': [];
    'add-files': [files: File[]];
    'remove-attachment': [id: string];
}>();
const draft = defineModel<string>({required: true});

const input = ref<HTMLTextAreaElement | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const expanded = ref(false);
const overflow = ref(false);
const composing = ref(false);
const dragging = ref(false);
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
    if (!composing.value && !props.locked && !props.uploading) emit('send');
}

function onPrimaryAction() {
    if (props.cancellable) emit('cancel');
    else send();
}

function openFilePicker() {
    if (props.locked || props.uploading) return;
    fileInput.value?.click();
}

function onFileChange(event: Event) {
    const target = event.target as HTMLInputElement | null;
    const files = target?.files ? Array.from(target.files) : [];
    if (target) target.value = '';
    if (files.length) emit('add-files', files);
}

function onPaste(event: ClipboardEvent) {
    if (props.locked || props.uploading) return;
    const items = event.clipboardData?.items;
    if (!items) return;
    const files: File[] = [];
    for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (item.kind === 'file') {
            const file = item.getAsFile();
            if (file) files.push(file);
        }
    }
    if (files.length) {
        event.preventDefault();
        emit('add-files', files);
    }
}

function onDragOver(event: DragEvent) {
    if (props.locked || props.uploading) return;
    if (event.dataTransfer?.types?.includes('Files')) {
        event.preventDefault();
        dragging.value = true;
    }
}

function onDragLeave() {
    dragging.value = false;
}

function onDrop(event: DragEvent) {
    dragging.value = false;
    if (props.locked || props.uploading) return;
    const files = event.dataTransfer?.files ? Array.from(event.dataTransfer.files) : [];
    if (files.length) {
        event.preventDefault();
        emit('add-files', files);
    }
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
    <div v-if="attachments.length || uploading" id="chat-attachments-bar" role="region" aria-label="待发送附件">
        <div v-for="att in attachments" :key="att.id" class="composer-attachment-chip"
            :class="{'is-image': att.kind === 'image'}">
            <img v-if="att.kind === 'image'" :src="att.url" :alt="att.name" class="composer-attachment-thumb">
            <div v-else class="composer-attachment-doc" :title="att.name">
                <svg class="composer-doc-icon" viewBox="0 0 20 20" width="15" height="15" aria-hidden="true">
                    <path fill="currentColor" d="M5 2a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7.4L11.6 2H5zm6 1.5L15.5 8H11V3.5z"/>
                </svg>
                <span class="composer-doc-name">{{ att.name }}</span>
                <span class="composer-doc-size">{{ formatFileSize(att.size) }}</span>
            </div>
            <button type="button" class="composer-attachment-remove" :aria-label="`移除 ${att.name}`"
                title="移除附件" :disabled="locked" @pointerdown="preserveInputFocus"
                @click="emit('remove-attachment', att.id)">×</button>
        </div>
        <span v-if="uploading" class="composer-uploading-tag">处理中…</span>
    </div>
    <div id="inputArea">
        <div id="chat-composer" :class="{'is-expanded': expanded, 'is-dragging': dragging}"
            @keydown.esc="onEscape" @dragover="onDragOver" @dragleave="onDragLeave" @drop="onDrop">
            <input ref="fileInput" type="file" multiple :accept="ACCEPTED_FILES" hidden @change="onFileChange">
            <button id="chat-attach-btn" type="button" aria-label="添加图片或文档" title="添加图片或文档（支持粘贴与拖入）"
                :disabled="locked || uploading" @pointerdown="preserveInputFocus" @click="openFilePicker">
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor"
                    stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M21.44 11.05l-8.49 8.49a5.5 5.5 0 0 1-7.78-7.78l9.19-9.19a3.5 3.5 0 0 1 4.95 4.95l-9.2 9.19a1.5 1.5 0 0 1-2.12-2.12l8.49-8.48"/>
                </svg>
            </button>
            <textarea id="userInput" ref="input" v-model="draft" autocomplete="off" rows="1"
                aria-label="聊天消息" placeholder="说点什么吧~" @keydown="onInputKey" @paste="onPaste"
                @compositionstart="composing = true" @compositionend="composing = false"
                @blur="composing = false" @change="composing = false"
                @input="composing && measureOverflow()"></textarea>
            <button id="chat-input-toggle" type="button" :aria-label="toggleLabel" aria-controls="userInput"
                :aria-expanded="expanded" :title="toggleLabel" :hidden="!expanded && !overflow"
                @pointerdown="preserveInputFocus" @click="setExpanded(!expanded)"></button>
        </div>
        <button id="chat-submit-btn" class="sendbtn" :class="{'is-stop': cancellable}" type="button"
            :disabled="(locked || uploading) && !cancellable" :aria-label="cancellable ? '停止生成' : undefined"
            @click="onPrimaryAction">
            {{ cancellable ? '停止' : (locked || uploading) ? '......' : '发送' }}
        </button>
    </div>
</template>
