const confirmation = () => document.getElementById('data-confirm-dialog') as HTMLDialogElement;
let pending = false;

export function closeConfirmation() {
    const dialog = confirmation();
    if (dialog.open) dialog.close('cancel');
}

export function confirmAction(title: string, description: string, action: string, requiresPassword = false): Promise<string | null> {
    const dialog = confirmation();
    if (pending) return Promise.resolve(null);
    const field = document.getElementById('data-confirm-password-field')!;
    const password = document.getElementById('data-confirm-password') as HTMLInputElement;
    const cancel = document.getElementById('data-confirm-cancel') as HTMLButtonElement;
    document.getElementById('data-confirm-title')!.textContent = title;
    document.getElementById('data-confirm-description')!.textContent = description;
    document.getElementById('data-confirm-submit')!.textContent = action;
    field.hidden = !requiresPassword;
    password.disabled = !requiresPassword;
    password.value = '';
    dialog.returnValue = '';
    cancel.onclick = closeConfirmation;
    dialog.showModal();
    pending = true;

    return new Promise(resolve => {
        dialog.addEventListener('close', () => {
            const result = dialog.returnValue === 'confirm' ? password.value : null;
            password.value = '';
            pending = false;
            resolve(result);
        }, {once: true});
    });
}
