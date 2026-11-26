export interface QuoteTarget {
    speaker: string;
    text: string;
}

export const TIME_DIVIDER_GAP_MS = 5 * 60 * 1000;
const MAX_QUOTE_SNIPPET_CHARS = 240;
const QUOTE_HEADER_PATTERN = /^【引用 ([^：\r\n]{1,40})：([\s\S]+?)】\r?\n([\s\S]+)$/;

function pad2(value: number): string {
    return String(value).padStart(2, '0');
}

function isSameLocalDay(first: Date, second: Date): boolean {
    return first.getFullYear() === second.getFullYear()
        && first.getMonth() === second.getMonth()
        && first.getDate() === second.getDate();
}

export function shouldShowTimeDivider(currentAt: number | null | undefined,
                                      previousAt: number | null | undefined): boolean {
    if (typeof currentAt !== 'number' || !Number.isFinite(currentAt)) return false;
    if (typeof previousAt !== 'number' || !Number.isFinite(previousAt)) return true;
    if (Math.abs(currentAt - previousAt) >= TIME_DIVIDER_GAP_MS) return true;
    return !isSameLocalDay(new Date(currentAt), new Date(previousAt));
}

export function formatChatTimestamp(timestamp: number, now: Date = new Date()): string {
    const target = new Date(timestamp);
    const timePart = `${pad2(target.getHours())}:${pad2(target.getMinutes())}`;
    if (isSameLocalDay(target, now)) return `今天 ${timePart}`;

    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    if (isSameLocalDay(target, yesterday)) return `昨天 ${timePart}`;

    const monthDay = `${target.getMonth() + 1}月${target.getDate()}日`;
    if (target.getFullYear() === now.getFullYear()) return `${monthDay} ${timePart}`;
    return `${target.getFullYear()}年${monthDay} ${timePart}`;
}

export function parseQuotedMessage(raw: string): {quote: QuoteTarget | null; body: string} {
    const match = QUOTE_HEADER_PATTERN.exec(raw);
    if (!match) return {quote: null, body: raw};
    return {
        quote: {speaker: match[1], text: match[2]},
        body: match[3],
    };
}

export function normalizeQuoteSnippet(raw: string): string {
    const unwrapped = parseQuotedMessage(raw).body.replace(/\r\n/g, '\n').trim();
    const compact = unwrapped.replace(/\s*\n+\s*/g, ' ');
    const chars = [...compact];
    return chars.length > MAX_QUOTE_SNIPPET_CHARS
        ? chars.slice(0, MAX_QUOTE_SNIPPET_CHARS).join('') + '…'
        : compact;
}

export function composeQuotedMessage(body: string, quote: QuoteTarget | null): string {
    if (!quote) return body;
    const speaker = quote.speaker.replace(/[【】：\r\n]/g, '').trim() || '消息';
    const snippet = normalizeQuoteSnippet(quote.text).replace(/】/g, '﹞');
    if (!snippet) return body;
    return `【引用 ${speaker}：${snippet}】\n${body}`;
}
