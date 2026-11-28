import {computed, nextTick, reactive, ref} from 'vue';
import {
    AuthRequiredError, CancelledRequestError, ChatNotStartedError, UserCancelledChatError,
    cancelChatAPI, chatAPI, getRequestGeneration, historyAPI, uploadAttachmentAPI
} from '../ts/api.ts';
import {composeQuotedMessage, parseQuotedMessage} from '../ts/chat_format.ts';
import type {QuoteTarget as QuoteDraft} from '../ts/chat_format.ts';
import {inputLimits} from '../ts/global_config.ts';
import type {ChatAttachment, ChatResponse, ConversationItem} from '../ts/global_config.ts';

export type {QuoteDraft};

export interface ChatTurn {
    kind: 'turn';
    id: string;
    round: number | null;
    user: string;
    replies: string[];
    createdAt?: number | null;
    attachments?: ChatAttachment[];
    quotedAttachmentIds?: string[];
    failure?: ChatNotStartedError;
}

interface ChatOptions {
    canSend: () => boolean;
    visionReady?: () => boolean;
    showAlert: (message: string) => void;
    onBusyChange: (busy: boolean) => void;
    scrollAfterRender: (forceBottom: boolean) => void;
}

interface ActiveTurnControl {
    turnId: string;
    controller: AbortController | null;
    cancelled: boolean;
    abort: () => void;
}

const firstReplyMetric = 'xiaowan.chat.first-reply';
const firstReplyDelayMs = 700;
const maxAttachmentsPerTurn = 4;
const maxImageDimension = 1280;
const compressThresholdBytes = 350 * 1024;
const BASE64_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

const visionUnsupportedMessage = '当前所选模型暂不支持图片识别，请切换到支持视觉的模型（如 deepseek-v4-flash），或仅发送文字与文档。';

function bytesToBase64(bytes: Uint8Array): string {
    let out = '';
    const len = bytes.length;
    for (let i = 0; i < len; i += 3) {
        const b0 = bytes[i];
        const b1 = i + 1 < len ? bytes[i + 1] : 0;
        const b2 = i + 2 < len ? bytes[i + 2] : 0;
        out += BASE64_CHARS[b0 >> 2];
        out += BASE64_CHARS[((b0 & 3) << 4) | (b1 >> 4)];
        out += i + 1 < len ? BASE64_CHARS[((b1 & 15) << 2) | (b2 >> 6)] : '=';
        out += i + 2 < len ? BASE64_CHARS[b2 & 63] : '=';
    }
    return out;
}

function isLikelyImageFile(file: {name?: string; type?: string}): boolean {
    const mime = (file.type || '').toLowerCase();
    if (mime.startsWith('image/')) return true;
    const lower = (file.name || '').toLowerCase();
    return /\.(png|jpe?g|webp|gif|bmp|svg)$/.test(lower);
}

async function prepareUploadPayload(file: File): Promise<{filename: string; mime_type: string; data_base64: string}> {
    const mime = (file.type || '').toLowerCase();
    const canCompress = (mime === 'image/jpeg' || mime === 'image/png' || mime === 'image/webp')
        && typeof createImageBitmap === 'function'
        && typeof document !== 'undefined'
        && typeof document.createElement === 'function';

    if (canCompress) {
        try {
            const bitmap = await createImageBitmap(file);
            try {
                const {width, height} = bitmap;
                const scale = Math.min(1, maxImageDimension / Math.max(width, height));
                if (scale < 1 || file.size > compressThresholdBytes) {
                    const targetW = Math.max(1, Math.round(width * scale));
                    const targetH = Math.max(1, Math.round(height * scale));
                    const canvas = document.createElement('canvas');
                    canvas.width = targetW;
                    canvas.height = targetH;
                    const ctx = canvas.getContext('2d');
                    if (ctx) {
                        ctx.drawImage(bitmap, 0, 0, targetW, targetH);
                        const dataUrl = canvas.toDataURL('image/webp', 0.84);
                        const finalUrl = dataUrl.startsWith('data:image/webp')
                            ? dataUrl
                            : canvas.toDataURL('image/jpeg', 0.85);
                        const comma = finalUrl.indexOf(',');
                        if (comma > 0) {
                            const outMime = finalUrl.startsWith('data:image/webp') ? 'image/webp' : 'image/jpeg';
                            const ext = outMime === 'image/webp' ? '.webp' : '.jpg';
                            const baseName = (file.name || 'image').replace(/\.[^.]+$/, '');
                            return {
                                filename: `${baseName}${ext}`,
                                mime_type: outMime,
                                data_base64: finalUrl.slice(comma + 1),
                            };
                        }
                    }
                }
            } finally {
                bitmap.close?.();
            }
        } catch {
            // 浏览器解码失败时回退原文件直传，由服务端魔数校验把关。
        }
    }

    const buffer = await file.arrayBuffer();
    return {
        filename: file.name || (isLikelyImageFile(file) ? 'image.png' : 'document.txt'),
        mime_type: file.type || '',
        data_base64: bytesToBase64(new Uint8Array(buffer)),
    };
}

function summarizeAttachments(items?: ChatAttachment[]): string {
    if (!items?.length) return '';
    return items.map(att => att.kind === 'image' ? '[图片]' : `[文档：${att.name}]`).join(' ');
}

export function useChat(options: ChatOptions) {
    const draft = ref('');
    const quote = ref<QuoteDraft | null>(null);
    const attachments = ref<ChatAttachment[]>([]);
    const uploadingCount = ref(0);
    const uploading = computed(() => uploadingCount.value > 0);
    const entries = ref<ChatTurn[]>([]);
    const busy = ref(false);
    const cancellable = ref(false);
    const isTyping = ref(false);
    let nextId = 0;
    let revision = 0;
    let finishReplyDelay: (() => void) | undefined;
    let activeControl: ActiveTurnControl | null = null;

    function currentOperation() {
        const startedRevision = revision;
        const generation = getRequestGeneration();
        return () => startedRevision === revision && generation === getRequestGeneration();
    }

    function isVisionSupported(): boolean {
        return options.visionReady ? options.visionReady() : true;
    }

    function setBusy(value: boolean) {
        if (busy.value === value) return;
        busy.value = value;
        options.onBusyChange(value);
    }

    function isUserCancelled(error: unknown): boolean {
        return typeof UserCancelledChatError === 'function' && error instanceof UserCancelledChatError;
    }

    function makeTurnId(): string {
        return `turn_${++nextId}_${Math.random().toString(36).slice(2, 10)}`;
    }

    function setQuote(speaker: string, text: string, turnAttachments?: ChatAttachment[]) {
        const cleaned = parseQuotedMessage(text).body.trim();
        const mediaSummary = summarizeAttachments(turnAttachments);
        const display = [mediaSummary, cleaned].filter(Boolean).join(' ');
        if (!display) return;
        const attachmentIds = turnAttachments?.map(item => item.id).filter(Boolean);
        const hasImage = Boolean(turnAttachments?.some(item => item.kind === 'image'));
        quote.value = {
            speaker,
            text: display,
            ...(attachmentIds?.length ? {attachmentIds, hasImage} : {}),
        };
    }

    function clearQuote() {
        quote.value = null;
    }

    function removeAttachment(id: string) {
        attachments.value = attachments.value.filter(item => item.id !== id);
    }

    async function addFiles(files: File[] | FileList) {
        if (busy.value || !options.canSend() || typeof uploadAttachmentAPI !== 'function') return;
        const list = Array.from(files);
        if (!list.length) return;
        const isCurrent = currentOperation();

        for (const file of list) {
            if (!isCurrent()) return;
            if (attachments.value.length >= maxAttachmentsPerTurn) {
                options.showAlert(`单条消息最多添加 ${maxAttachmentsPerTurn} 个附件`);
                return;
            }
            if (isLikelyImageFile(file) && !isVisionSupported()) {
                options.showAlert(visionUnsupportedMessage);
                continue;
            }
            uploadingCount.value++;
            try {
                const payload = await prepareUploadPayload(file);
                if (!isCurrent()) return;
                const saved = await uploadAttachmentAPI(payload);
                if (!isCurrent()) return;
                if (saved.kind === 'image' && !isVisionSupported()) {
                    options.showAlert(visionUnsupportedMessage);
                    continue;
                }
                if (attachments.value.length < maxAttachmentsPerTurn) {
                    attachments.value.push(saved);
                    options.scrollAfterRender(false);
                }
            } catch (error) {
                if (!isCurrent() || error instanceof AuthRequiredError || error instanceof CancelledRequestError) return;
                options.showAlert(error instanceof Error ? error.message : '附件上传失败，请稍后重试');
            } finally {
                if (uploadingCount.value > 0) uploadingCount.value--;
            }
        }
    }

    function mergeHistory(history: ConversationItem[]) {
        const byRound = new Map<number, ConversationItem>();
        for (const item of history) byRound.set(item.conversation_round, item);

        const displayed = new Set<number>();
        for (const entry of entries.value) {
            if (entry.kind !== 'turn' || entry.round === null) continue;
            displayed.add(entry.round);
            const savedItem = byRound.get(entry.round);
            if (savedItem) {
                if (entry.createdAt == null && typeof savedItem.created_at === 'number') {
                    entry.createdAt = savedItem.created_at;
                }
                if (!entry.attachments?.length && savedItem.attachments?.length) {
                    entry.attachments = savedItem.attachments;
                }
            }
        }

        const missing: (ChatTurn & {round: number})[] = [];
        for (const item of [...history].sort((first, second) => first.conversation_round - second.conversation_round)) {
            if (displayed.has(item.conversation_round)) continue;
            displayed.add(item.conversation_round);
            missing.push({
                kind: 'turn',
                id: `round-${item.conversation_round}`,
                round: item.conversation_round,
                user: item.user,
                replies: item.assistant,
                ...(typeof item.created_at === 'number' ? {createdAt: item.created_at} : {}),
                ...(item.attachments?.length ? {attachments: item.attachments} : {}),
            });
        }
        if (!missing.length) return;

        const merged: ChatTurn[] = [];
        let next = 0;
        for (const entry of entries.value) {
            if (entry.round !== null) {
                while (next < missing.length && missing[next].round < entry.round) merged.push(missing[next++]);
            }
            merged.push(entry);
        }
        entries.value = merged.concat(missing.slice(next));
    }

    async function loadHistory() {
        const isCurrent = currentOperation();
        const history = await historyAPI(0);
        if (!isCurrent()) return;
        mergeHistory(history.history_messages);
        options.scrollAfterRender(true);
    }

    async function markOrReconcileFailure(turn: ChatTurn, maxRoundBefore: number, isCurrent: () => boolean) {
        turn.replies.splice(0, turn.replies.length);
        try {
            const history = await historyAPI(0);
            if (!isCurrent()) return;
            const savedMatch = history.history_messages.find(
                item => item.conversation_round > maxRoundBefore && item.user === turn.user,
            );
            if (savedMatch) {
                if (typeof savedMatch.created_at === 'number') turn.createdAt = savedMatch.created_at;
                if (savedMatch.attachments?.length) turn.attachments = savedMatch.attachments;
                confirmRound(turn, savedMatch.conversation_round);
                turn.replies.push(...savedMatch.assistant);
                mergeHistory(history.history_messages);
                options.scrollAfterRender(false);
                return;
            }
            mergeHistory(history.history_messages);
        } catch {
            // 网络暂不可达时静默跳过历史同步，直接保留原气泡并标红色感叹号。
        }
        if (!isCurrent()) return;
        turn.failure = new ChatNotStartedError('消息发送失败，点击感叹号可重新发送', true);
        options.scrollAfterRender(false);
    }

    function waitForReplyDelay(milliseconds: number) {
        return new Promise<void>(resolve => {
            const timer = setTimeout(finish, milliseconds);
            finishReplyDelay = finish;
            function finish() {
                clearTimeout(timer);
                finishReplyDelay = undefined;
                resolve();
            }
        });
    }

    function confirmRound(turn: ChatTurn, round: number) {
        turn.round = round;
        const optimisticIndex = entries.value.indexOf(turn);
        if (optimisticIndex < 0) return;
        let position = optimisticIndex;
        // 历史可能先于发送结果返回，确认后按轮次排列并保留本地气泡的键。
        for (const [index, entry] of entries.value.entries()) {
            if (entry === turn || entry.kind !== 'turn' || entry.round === null) continue;
            if (entry.round === round) {
                entries.value[index] = turn;
                entries.value.splice(optimisticIndex, 1);
                return;
            }
            if (entry.round > round) {
                position = Math.min(position, index);
                break;
            }
            position = Math.max(position, index);
        }
        if (position === optimisticIndex) return;
        entries.value.splice(optimisticIndex, 1);
        entries.value.splice(position, 0, turn);
    }

    function recordFirstReplyTiming(startedAt: number, isCurrent: () => boolean) {
        void nextTick(() => {
            if (!isCurrent()) return;
            performance.clearMeasures(firstReplyMetric);
            performance.measure(firstReplyMetric, {start: startedAt, end: performance.now()});
        });
    }

    async function revealReplies(turn: ChatTurn, data: ChatResponse, firstReplyDelay: Promise<void>,
                                 startedAt: number, streamedCount: number, isCurrent: () => boolean) {
        if (typeof data.created_at === 'number') turn.createdAt = data.created_at;
        if (data.attachments?.length) turn.attachments = data.attachments;
        if (data.saved && data.conversation_round !== null) confirmRound(turn, data.conversation_round);
        if (streamedCount === 0 && data.response.length) await firstReplyDelay;
        if (!isCurrent()) return;
        isTyping.value = false;
        const remaining = data.response.slice(streamedCount);
        for (const [index, text] of remaining.entries()) {
            if (!isCurrent()) return;
            turn.replies.push(text);
            options.scrollAfterRender(false);
            if (streamedCount === 0 && index === 0) {
                recordFirstReplyTiming(startedAt, isCurrent);
            }
            if (index < remaining.length - 1) {
                const delay = streamedCount > 0
                    ? Math.min(350 + 25 * text.length, 1000)
                    : Math.min(1000 + 100 * text.length, 5000);
                await waitForReplyDelay(delay);
            }
        }
        if (isCurrent() && !data.saved) options.showAlert('本轮对话未保存。回复仍可查看，请检查数据库连接。');
    }

    // 用户主动停止发送时，拆回原引用条、附件与输入草稿，不保留半截气泡。
    function restoreCancelledTurn(turn: ChatTurn, hadQuoteImage?: boolean) {
        const parsed = parseQuotedMessage(turn.user);
        if (parsed.quote && !quote.value) {
            quote.value = {
                ...parsed.quote,
                ...(turn.quotedAttachmentIds?.length ? {attachmentIds: [...turn.quotedAttachmentIds], hasImage: hadQuoteImage} : {}),
            };
        }
        if (turn.attachments?.length) {
            const existingIds = new Set(attachments.value.map(item => item.id));
            const restored = turn.attachments.filter(item => !existingIds.has(item.id));
            attachments.value = [...restored, ...attachments.value].slice(0, maxAttachmentsPerTurn);
        }
        if (!draft.value.trim()) draft.value = parsed.body;
        else if (parsed.body && draft.value !== parsed.body) draft.value = `${parsed.body}\n${draft.value}`;
    }

    async function runTurnRequest(
        turn: ChatTurn,
        invoke: (control: ActiveTurnControl, onBubble: (text: string) => void) => Promise<ChatResponse>,
        onAbortRestore: () => void,
        onErrorFallback: (error: unknown, isCurrent: () => boolean) => Promise<void> | void,
    ) {
        const isCurrent = currentOperation();
        const startedAt = performance.now();
        const turnId = makeTurnId();
        const controller = typeof AbortController === 'function' ? new AbortController() : null;
        let settleCancel: (() => void) | undefined;
        const cancelGate = new Promise<never>((_, reject) => {
            settleCancel = () => reject(typeof UserCancelledChatError === 'function'
                ? new UserCancelledChatError() : new Error('cancelled'));
        });
        cancelGate.catch(() => {});

        const control: ActiveTurnControl = {
            turnId,
            controller,
            cancelled: false,
            abort() {
                if (this.cancelled) return;
                this.cancelled = true;
                finishReplyDelay?.();
                this.controller?.abort();
                settleCancel?.();
            },
        };
        activeControl = control;
        setBusy(true);
        cancellable.value = true;
        options.scrollAfterRender(true);

        // 请求与显示延时并行。延时结束仍未收到回复时，才显示正在输入；流式多气泡按真人节奏逐条上屏。
        let responsePending = true;
        let firstDelayDone = false;
        let streamActive = true;
        let streamFailed = false;
        let streamedCount = 0;
        let lastBubbleShownAt = 0;
        let postBubbleTypingTimer: ReturnType<typeof setTimeout> | undefined;
        const pendingStreamBubbles: string[] = [];
        let drainPromise: Promise<void> | null = null;

        const clearPostBubbleTyping = () => {
            if (postBubbleTypingTimer !== undefined) {
                clearTimeout(postBubbleTypingTimer);
                postBubbleTypingTimer = undefined;
            }
        };

        const schedulePostBubbleTyping = () => {
            clearPostBubbleTyping();
            if (!streamActive || streamFailed || pendingStreamBubbles.length > 0) return;
            postBubbleTypingTimer = setTimeout(() => {
                postBubbleTypingTimer = undefined;
                if (!streamActive || streamFailed || pendingStreamBubbles.length > 0 || control.cancelled || !isCurrent()) return;
                isTyping.value = true;
                options.scrollAfterRender(false);
            }, 450);
        };

        const firstReplyDelay = waitForReplyDelay(firstReplyDelayMs).then(() => {
            firstDelayDone = true;
            if (!responsePending || streamFailed || control.cancelled || !isCurrent()) return;
            isTyping.value = true;
            options.scrollAfterRender(false);
        });

        async function drainStreamBubbles() {
            while (pendingStreamBubbles.length > 0) {
                if (!isCurrent() || control.cancelled || streamFailed) return;
                if (streamedCount === 0 && !firstDelayDone) {
                    await firstReplyDelay;
                    if (!isCurrent() || control.cancelled || streamFailed) return;
                }
                const nextText = pendingStreamBubbles.shift()!;
                const prevText = turn.replies.length > 0 ? turn.replies[turn.replies.length - 1] : null;
                if (prevText !== null) {
                    const targetGapMs = Math.min(750 + 60 * prevText.length, 2800);
                    const elapsedMs = Math.max(0, performance.now() - lastBubbleShownAt);
                    const remainingGapMs = targetGapMs - elapsedMs;
                    if (remainingGapMs > 40) {
                        clearPostBubbleTyping();
                        isTyping.value = true;
                        options.scrollAfterRender(false);
                        await waitForReplyDelay(remainingGapMs);
                        if (!isCurrent() || control.cancelled || streamFailed) return;
                    }
                }
                clearPostBubbleTyping();
                isTyping.value = false;
                turn.replies.push(nextText);
                streamedCount++;
                lastBubbleShownAt = performance.now();
                options.scrollAfterRender(false);
                if (streamedCount === 1) recordFirstReplyTiming(startedAt, isCurrent);
                if (pendingStreamBubbles.length === 0 && streamActive && !streamFailed) {
                    schedulePostBubbleTyping();
                }
            }
        }

        const ensureDrainRunning = () => {
            if (!drainPromise && pendingStreamBubbles.length > 0 && !streamFailed) {
                drainPromise = drainStreamBubbles().finally(() => {
                    drainPromise = null;
                    if (pendingStreamBubbles.length > 0 && isCurrent() && !control.cancelled && !streamFailed) {
                        ensureDrainRunning();
                    }
                });
            }
        };

        const handleStreamedBubble = (text: string) => {
            if (!isCurrent() || control.cancelled || streamFailed || !text.trim()) return;
            responsePending = false;
            pendingStreamBubbles.push(text);
            ensureDrainRunning();
        };

        try {
            let data: ChatResponse;
            try {
                data = await Promise.race([invoke(control, handleStreamedBubble), cancelGate]).finally(() => {
                    responsePending = false;
                    streamActive = false;
                    clearPostBubbleTyping();
                    if (activeControl === control) cancellable.value = false;
                });
            } catch (error) {
                streamFailed = true;
                pendingStreamBubbles.length = 0;
                clearPostBubbleTyping();
                finishReplyDelay?.();
                if (drainPromise) await drainPromise;
                if (!isCurrent() || error instanceof AuthRequiredError || error instanceof CancelledRequestError) return;
                isTyping.value = false;
                if (control.cancelled || isUserCancelled(error)) {
                    onAbortRestore();
                    return;
                }
                await onErrorFallback(error, isCurrent);
                return;
            }

            while (drainPromise) {
                await drainPromise;
            }
            if (!isCurrent() || control.cancelled) {
                if (control.cancelled && isCurrent()) onAbortRestore();
                return;
            }
            await revealReplies(turn, data, firstReplyDelay, startedAt, streamedCount, isCurrent);
        } finally {
            clearPostBubbleTyping();
            if (activeControl === control) {
                activeControl = null;
                cancellable.value = false;
            }
            if (isCurrent()) {
                finishReplyDelay?.();
                isTyping.value = false;
                setBusy(false);
            }
        }
    }

    async function submitMessage(turn: ChatTurn, hadQuoteImage = false) {
        const attachmentIds = turn.attachments?.map(item => item.id);
        const quotedAttachmentIds = turn.quotedAttachmentIds;
        const maxRoundBefore = entries.value.reduce(
            (max, item) => item.round !== null && item.round > max ? item.round : max,
            -1,
        );
        await runTurnRequest(
            turn,
            (control, onBubble) => typeof cancelChatAPI === 'function'
                ? chatAPI(turn.user, {
                    turnId: control.turnId,
                    signal: control.controller?.signal,
                    onBubble,
                    ...(attachmentIds?.length ? {attachmentIds} : {}),
                    ...(quotedAttachmentIds?.length ? {quotedAttachmentIds} : {}),
                })
                : chatAPI(turn.user),
            () => {
                entries.value = entries.value.filter(entry => entry !== turn);
                restoreCancelledTurn(turn, hadQuoteImage);
            },
            async (error, isCurrent) => {
                if (error instanceof ChatNotStartedError) {
                    turn.replies.splice(0, turn.replies.length);
                    turn.failure = error;
                    options.scrollAfterRender(false);
                } else {
                    await markOrReconcileFailure(turn, maxRoundBefore, isCurrent);
                }
            },
        );
    }

    async function sendMessage() {
        if (busy.value || uploadingCount.value > 0 || !options.canSend()) return;
        const body = draft.value.trim();
        const staged = [...attachments.value];
        if (!body && !staged.length) return;

        const hasOwnImage = staged.some(item => item.kind === 'image');
        const hasQuoteImage = Boolean(quote.value?.hasImage);
        if ((hasOwnImage || hasQuoteImage) && !isVisionSupported()) {
            options.showAlert(visionUnsupportedMessage);
            return;
        }

        const composed = composeQuotedMessage(body, quote.value);
        const limit = inputLimits.max_message_chars;
        if (composed.length > limit && [...composed].length > limit) {
            options.showAlert(quote.value ? `聊天消息（含引用内容）最多 ${limit} 个字符` : `聊天消息最多 ${limit} 个字符`);
            return;
        }

        const quotedIds = quote.value?.attachmentIds?.length ? [...quote.value.attachmentIds] : undefined;
        draft.value = '';
        quote.value = null;
        attachments.value = [];
        const turn = reactive<ChatTurn>({
            kind: 'turn',
            id: `local-${++nextId}`,
            round: null,
            user: composed,
            replies: [],
            ...(staged.length ? {attachments: staged} : {}),
            ...(quotedIds ? {quotedAttachmentIds: quotedIds} : {}),
            ...(typeof cancelChatAPI === 'function' ? {createdAt: Date.now()} : {}),
        });
        entries.value.push(turn);
        await submitMessage(turn, hasQuoteImage);
    }

    function cancelSend() {
        const control = activeControl;
        if (!control || !cancellable.value) return;
        control.abort();
        if (typeof cancelChatAPI === 'function') {
            void cancelChatAPI(control.turnId).catch(() => {});
        }
    }

    async function activateFailure(turn: ChatTurn) {
        if (!turn.failure || !entries.value.includes(turn)) return;
        if (!turn.failure.retryable) {
            options.showAlert(turn.failure.message);
            return;
        }
        if (busy.value || uploadingCount.value > 0 || !options.canSend()) return;
        if (turn.attachments?.some(item => item.kind === 'image') && !isVisionSupported()) {
            options.showAlert(visionUnsupportedMessage);
            return;
        }

        turn.failure = undefined;
        entries.value = entries.value.filter(entry => entry !== turn);
        entries.value.push(turn);
        await submitMessage(turn);
    }

    function clearHistory() {
        revision++;
        activeControl?.abort();
        activeControl = null;
        finishReplyDelay?.();
        entries.value = [];
        cancellable.value = false;
        isTyping.value = false;
        setBusy(false);
    }

    function reset() {
        clearHistory();
        draft.value = '';
        quote.value = null;
        attachments.value = [];
        uploadingCount.value = 0;
        performance.clearMeasures(firstReplyMetric);
    }

    return {
        draft, quote, attachments, uploading, entries, busy, cancellable, isTyping,
        setQuote, clearQuote, addFiles, removeAttachment, loadHistory, sendMessage, cancelSend,
        activateFailure, clearHistory, reset,
    };
}
