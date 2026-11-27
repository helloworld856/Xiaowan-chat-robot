import {nextTick, reactive, ref} from 'vue';
import {
    AuthRequiredError, CancelledRequestError, ChatNotStartedError, chatAPI, getRequestGeneration, historyAPI
} from '../ts/api.ts';
import {inputLimits} from '../ts/global_config.ts';
import type {ConversationItem} from '../ts/global_config.ts';

export interface ChatTurn {
    kind: 'turn';
    id: string;
    round: number | null;
    user: string;
    replies: string[];
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

const firstReplyMetric = 'xiaowan.chat.first-reply';
const firstReplyDelayMs = 700;

export function useChat(options: ChatOptions) {
    const draft = ref('');
    const entries = ref<ChatEntry[]>([]);
    const busy = ref(false);
    const isTyping = ref(false);
    let nextId = 0;
    let revision = 0;
    let finishReplyDelay: (() => void) | undefined;

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

    function mergeHistory(history: ConversationItem[]) {
        const displayed = new Set(entries.value.flatMap(entry =>
            entry.kind === 'turn' && entry.round !== null ? [entry.round] : []));
        const missing: (ChatTurn & {round: number})[] = [];
        for (const item of [...history].sort((first, second) => first.conversation_round - second.conversation_round)) {
            if (displayed.has(item.conversation_round)) continue;
            displayed.add(item.conversation_round);
            missing.push({
                kind: 'turn', id: `round-${item.conversation_round}`, round: item.conversation_round,
                user: item.user, replies: item.assistant
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

    async function submitMessage(turn: ChatTurn) {
        const isCurrent = currentOperation();
        const startedAt = performance.now();
        setBusy(true);
        options.scrollAfterRender(true);

        // 请求与显示延时并行。延时结束仍未收到回复时，才显示正在输入。
        let responsePending = true;
        const firstReplyDelay = waitForReplyDelay(firstReplyDelayMs).then(() => {
            if (!responsePending || !isCurrent()) return;
            isTyping.value = true;
            options.scrollAfterRender(false);
        });

        try {
            let data;
            try {
                data = await chatAPI(turn.user).finally(() => { responsePending = false; });
            } catch (error) {
                if (!isCurrent() || error instanceof AuthRequiredError || error instanceof CancelledRequestError) return;
                finishReplyDelay?.();
                isTyping.value = false;
                if (error instanceof ChatNotStartedError) {
                    turn.failure = error;
                    options.showAlert(error.message);
                } else {
                    await reconcileDelivery(turn, isCurrent);
                }
                return;
            }

            if (!isCurrent()) return;
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
        } finally {
            if (isCurrent()) {
                finishReplyDelay?.();
                isTyping.value = false;
                setBusy(false);
            }
        }
    }

    async function sendMessage() {
        if (busy.value || !options.canSend()) return;
        const user = draft.value.trim();
        if (!user) return;
        const limit = inputLimits.max_message_chars;
        if (user.length > limit && [...user].length > limit) {
            options.showAlert(`聊天消息最多 ${limit} 个字符`);
            return;
        }

        draft.value = '';
        const turn = reactive<ChatTurn>({
            kind: 'turn', id: `local-${++nextId}`, round: null, user, replies: []
        });
        entries.value.push(turn);
        await submitMessage(turn);
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

    function clearHistory() {
        revision++;
        finishReplyDelay?.();
        entries.value = [];
        isTyping.value = false;
        setBusy(false);
    }

    function reset() {
        clearHistory();
        draft.value = '';
        performance.clearMeasures(firstReplyMetric);
    }

    return {draft, entries, busy, isTyping, loadHistory, sendMessage, activateFailure, clearHistory, reset};
}
