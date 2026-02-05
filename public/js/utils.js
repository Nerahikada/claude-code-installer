const VALID_MODES = ['install', 'token'];
const VALID_OS = ['linux', 'macos', 'windows'];

function detectOSFromBrowser() {
    const ua = navigator.userAgent.toLowerCase();
    const platform = navigator.platform.toLowerCase();

    if (platform.includes('win') || ua.includes('windows')) {
        return 'windows';
    }
    if (platform.includes('mac') || ua.includes('mac')) {
        return 'macos';
    }
    return 'linux';
}

function parseHash() {
    const hash = window.location.hash.slice(1);
    const [mode, os] = hash.split('/');
    return { mode, os };
}

export function getInitialMode() {
    const { mode } = parseHash();
    if (mode && VALID_MODES.includes(mode)) {
        return mode;
    }
    return 'install';
}

export function getInitialOS() {
    const { os } = parseHash();
    if (os && VALID_OS.includes(os)) {
        return os;
    }
    return detectOSFromBrowser();
}

export function getLanguageForOS(os) {
    return os === 'windows' ? 'powershell' : 'bash';
}

export async function copyToClipboard(text) {
    if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        return;
    }

    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.cssText = 'position:fixed;opacity:0;pointer-events:none';
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand('copy');
    document.body.removeChild(textarea);
}
