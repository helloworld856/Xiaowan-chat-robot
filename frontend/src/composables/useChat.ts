import {nextTick, reactive, ref} from 'vue';
import {
    AuthRequiredError, CancelledRequestError, ChatNotStartedError, UserCancelledChatError,
    cancelChatAPI, chatAPI, deleteHistoryRoundAPI, getRequestGeneration,
    historyAPI, regenerateChatAPI
} from '../ts/api.ts';
import {composeQuotedMessage, parseQuotedMessage} from '../ts/chat_format.ts';
import type {QuoteTarget as QuoteDraft} from '../ts/chat_format.ts';
import {inputLimits} from '../ts/global_config.ts';
import type {ChatResponse, ConversationItem} from '../ts/global_config.ts';

export type {QuoteDraft};

export interface ChatTurn {
    kind: 'turn';
    id: string;
    round: number | null;
    user: string;
    replies: string[];
    createdAt?: number | null;
    failure?: ChatNotStartedError;
}

interface DeliveryNotice {
    kind: 'notice';
    id: string;
    user: string;
    message: string;
}

type ChatEntry = ChatTurn | DeliveryNotice;

interface ChatOptions {
    canSend: () => boolean;
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

export function useChat(options: ChatOptions) {
    const draft = ref('');
    const quote = ref<QuoteDraft | null>(null);
    const entries = ref<ChatEntry[]>([]);
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

    function setQuote(speaker: string, text: string) {
        const cleaned = parseQuotedMessage(text).body.trim();
        if (!cleaned) return;
        quote.value = {speaker, text: cleaned};
    }

    function clearQuote() {
        quote.value = null;
    }

    function mergeHistory(history: ConversationItem[]) {
        const byRound = new Map<number, ConversationItem>();
        for (const item of history) byRound.set(item.conversation_round, item);

        const displayed = new Set<number>();
        for (const entry of entries.value) {
            if (entry.kind !== 'turn' || entry.round === null) continue;
            displayed.add(entry.round);
            const savedItem = byRound.get(entry.round);
            if (savedItem && entry.createdAt == null && typeof savedItem.created_at === 'number') {
                entry.createdAt = savedItem.created_at;
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
            });
        }
        if (!missing.length) return;

        const merged: ChatEntry[] = [];
        let next = 0;
        for (const entry of entries.value) {
            if (entry.kind === 'turn' && entry.round !== null) {
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

    async function reconcileDelivery(turn: ChatTurn, isCurrent: () => boolean) {
        entries.value = entries.value.filter(entry => entry.id !== turn.id);
        let message = '已刷新聊天记录，此消息的发送结果仍需确认。请核对后再发送。';
        try {
            const history = await historyAPI(0);
            if (isCurrent()) mergeHistory(history.history_messages);
        } catch {
            message = '暂时无法核对这条消息，请查看聊天记录后再发送。';
        }
        if (!isCurrent()) return;
        entries.value.push({kind: 'notice', id: turn.id, user: turn.user, message});
        options.scrollAfterRender(true);
        options.showAlert(message);
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

    async function revealReplies(turn: ChatTurn, data: ChatResponse, firstReplyDelay: Promise<void>,
                                 startedAt: number, isCurrent: () => boolean) {
        if (typeof data.created_at === 'number') turn.createdAt = data.created_at;
        if (data.saved && data.conversation_round !== null) confirmRound(turn, data.conversation_round);
        if (data.response.length) await firstReplyDelay;
        if (!isCurrent()) return;
        isTyping.value = false;
        for (const [index, text] of data.response.entries()) {
            if (!isCurrent()) return;
            turn.replies.push(text);
            options.scrollAfterRender(false);
            if (index === 0) {
                void nextTick(() => {
                    if (!isCurrent()) return;
                    performance.clearMeasures(firstReplyMetric);
                    performance.measure(firstReplyMetric, {start: startedAt, end: performance.now()});
                });
            }
            if (index < data.response.length - 1) await waitForReplyDelay(Math.min(1000 + 100 * text.length, 5000));
        }
        if (isCurrent() && !data.saved) options.showAlert('本轮对话未保存。回复仍可查看，请检查数据库连接。');
    }

    // 用户主动停止发送时，拆回原引用条与输入草稿，不保留半截气泡。
    function restoreCancelledDraft(rawUserText: string) {
        const parsed = parseQuotedMessage(rawUserText);
        if (parsed.quote && !quote.value) quote.value = parsed.quote;
        if (!draft.value.trim()) draft.value = parsed.body;
        else if (draft.value !== parsed.body) draft.value = `${parsed.body}\n${draft.value}`;
    }

    async function runTurnRequest(
        turn: ChatTurn,
        invoke: (control: ActiveTurnControl) => Promise<ChatResponse>,
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
                this.controller?.abort();
                settleCancel?.();
            },
        };
        activeControl = control;
        setBusy(true);
        cancellable.value = true;
        options.scrollAfterRender(true);

        // 请求与显示延时并行。延时结束仍未收到回复时，才显示正在输入。
        let responsePending = true;
        const firstReplyDelay = waitForReplyDelay(firstReplyDelayMs).then(() => {
            if (!responsePending || control.cancelled || !isCurrent()) return;
            isTyping.value = true;
            options.scrollAfterRender(false);
        });

        try {
            let data: ChatResponse;
            try {
                data = await Promise.race([invoke(control), cancelGate]).finally(() => {
                    responsePending = false;
                    if (activeControl === control) cancellable.value = false;
                });
            } catch (error) {
                if (!isCurrent() || error instanceof AuthRequiredError || error instanceof CancelledRequestError) return;
                finishReplyDelay?.();
                isTyping.value = false;
                if (control.cancelled || isUserCancelled(error)) {
                    onAbortRestore();
                    return;
                }
                await onErrorFallback(error, isCurrent);
                return;
            }

            if (!isCurrent() || control.cancelled) {
                if (control.cancelled && isCurrent()) onAbortRestore();
                return;
            }
            await revealReplies(turn, data, firstReplyDelay, startedAt, isCurrent);
        } finally {
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

    async function submitMessage(turn: ChatTurn) {
        await runTurnRequest(
            turn,
            control => typeof cancelChatAPI === 'function'
                ? chatAPI(turn.user, {turnId: control.turnId, signal: control.controller?.signal})
                : chatAPI(turn.user),
            () => {
                entries.value = entries.value.filter(entry => entry !== turn);
                restoreCancelledDraft(turn.user);
            },
            async (error, isCurrent) => {
                if (error instanceof ChatNotStartedError) {
                    turn.failure = error;
                    options.showAlert(error.message);
                } else {
                    await reconcileDelivery(turn, isCurrent);
                }
            },
        );
    }

    async function sendMessage() {
        if (busy.value || !options.canSend()) return;
        const body = draft.value.trim();
        if (!body) return;
        const composed = composeQuotedMessage(body, quote.value);
        const limit = inputLimits.max_message_chars;
        if (composed.length > limit && [...composed].length > limit) {
            options.showAlert(quote.value ? `聊天消息（含引用内容）最多 ${limit} 个字符` : `聊天消息最多 ${limit} 个字符`);
            return;
        }

        draft.value = '';
        quote.value = null;
        const turn = reactive<ChatTurn>({
            kind: 'turn',
            id: `local-${++nextId}`,
            round: null,
            user: composed,
            replies: [],
            ...(typeof cancelChatAPI === 'function' ? {createdAt: Date.now()} : {}),
        });
        entries.value.push(turn);
        await submitMessage(turn);
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
        if (busy.value || !options.canSend()) return;

        turn.failure = undefined;
        entries.value = entries.value.filter(entry => entry !== turn);
        entries.value.push(turn);
        await submitMessage(turn);
    }

    async function regenerateTurn(turn: ChatTurn) {
        if (busy.value || !options.canSend()) return;
        if (entries.value[entries.value.length - 1] !== turn) {
            options.showAlert('只能重新生成最新一轮对话');
            return;
        }
        if (turn.round === null) {
            turn.failure = undefined;
            turn.replies = [];
            await submitMessage(turn);
            return;
        }

        const round = turn.round;
        const previousReplies = [...turn.replies];
        const previousCreatedAt = turn.createdAt;
        turn.replies = [];

        await runTurnRequest(
            turn,
            control => regenerateChatAPI(round, {turnId: control.turnId, signal: control.controller?.signal}),
            () => {
                turn.replies = previousReplies;
                turn.createdAt = previousCreatedAt;
            },
            error => {
                turn.replies = previousReplies;
                turn.createdAt = previousCreatedAt;
                options.showAlert(error instanceof Error ? error.message : '重新生成失败，请稍后重试');
            },
        );
    }

    async function deleteTurn(turn: ChatTurn) {
        if (busy.value || !options.canSend() || !entries.value.includes(turn)) return false;
        const isCurrent = currentOperation();
        if (turn.round !== null) {
            setBusy(true);
            try {
                await deleteHistoryRoundAPI(turn.round);
            } catch (error) {
                if (isCurrent() && !(error instanceof AuthRequiredError) && !(error instanceof CancelledRequestError)) {
                    options.showAlert(error instanceof Error ? error.message : '删除该轮对话失败，请稍后重试');
                }
                return false;
            } finally {
                if (isCurrent()) setBusy(false);
            }
        }
        if (!isCurrent()) return false;
        entries.value = entries.value.filter(entry => entry !== turn);
        return true;
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
        performance.clearMeasures(firstReplyMetric);
    }

    return {
        draft, quote, entries, busy, cancellable, isTyping,
        setQuote, clearQuote, loadHistory, sendMessage, cancelSend,
        activateFailure, regenerateTurn, deleteTurn, clearHistory, reset,
    };
}
