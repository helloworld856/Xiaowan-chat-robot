export interface ConfirmOptions {
    title: string;
    description: string;
    action: string;
    password?: boolean;
    destructive?: boolean;
    variant?: 'logout';
}

export function openDialog(dialog: HTMLDialogElement) {
    dialog.classList.remove('closing');
    dialog.inert = false;
    dialog.classList.add('open');
    if (!dialog.open) dialog.showModal();
}

export function resetDialog(dialog: HTMLDialogElement) {
    if (dialog.open) dialog.close();
    dialog.classList.remove('open', 'closing');
    dialog.inert = false;
}

export async function closeDialog(dialog: HTMLDialogElement) {
    if (!dialog.open || dialog.classList.contains('closing')) return;
    dialog.classList.add('closing');
    dialog.inert = true;
    const animations = [...dialog.getAnimations(), ...Array.from(dialog.children).flatMap(child => child.getAnimations())];
    await Promise.allSettled(animations.map(animation => animation.finished));
    if (dialog.classList.contains('closing')) resetDialog(dialog);
}
