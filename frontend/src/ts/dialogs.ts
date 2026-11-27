export interface ConfirmOptions {
    title: string;
    description: string;
    action: string;
    password?: boolean;
    destructive?: boolean;
    variant?: 'logout';
}

const pendingClosures = new WeakMap<HTMLDialogElement, symbol>();

export function openDialog(dialog: HTMLDialogElement) {
    pendingClosures.delete(dialog);
    dialog.classList.remove('closing');
    dialog.inert = false;
    dialog.classList.add('open');
    if (!dialog.open) dialog.showModal();
}

export function resetDialog(dialog: HTMLDialogElement) {
    pendingClosures.delete(dialog);
    if (dialog.open) dialog.close();
    dialog.classList.remove('open', 'closing');
    dialog.inert = false;
}

export async function closeDialog(dialog: HTMLDialogElement) {
    if (!dialog.open || dialog.classList.contains('closing')) return;
    const operation = Symbol();
    pendingClosures.set(dialog, operation);
    dialog.classList.add('closing');
    dialog.inert = true;
    const animations = [...dialog.getAnimations(), ...Array.from(dialog.children).flatMap(child => child.getAnimations())];
    await Promise.allSettled(animations.map(animation => animation.finished));
    if (pendingClosures.get(dialog) === operation) resetDialog(dialog);
}
