import { CREDENTIAL_PLACEHOLDER, COPY_ICON_DEFAULT, COPY_ICON_SUCCESS, COPY_FEEDBACK_DURATION } from './config.js';
import { fetchCommand, fetchCredentials } from './api.js';
import { getInitialOS, getInitialMode, getLanguageForOS, copyToClipboard } from './utils.js';

function createApp(credentials) {
    const elements = {
        codeDisplay: document.getElementById('code-display'),
        copyBtn: document.getElementById('copy-btn'),
        copyIcon: document.getElementById('copy-icon'),
        modeTabs: document.getElementById('mode-tabs'),
        osGroup: document.getElementById('os-group')
    };

    const state = {
        mode: getInitialMode(),
        os: getInitialOS()
    };

    async function getCommand() {
        let command = await fetchCommand(state.mode, state.os);
        if (credentials) {
            command = command.replace(CREDENTIAL_PLACEHOLDER, credentials);
        }
        return command;
    }

    async function updateDisplay() {
        const code = await getCommand();
        const lang = getLanguageForOS(state.os);

        elements.codeDisplay.className = `language-${lang}`;
        elements.codeDisplay.textContent = code;
        Prism.highlightElement(elements.codeDisplay);
    }

    function updateTabSelection() {
        document.querySelectorAll('.tab').forEach(tab => {
            const isActive = tab.dataset.mode === state.mode;
            tab.classList.toggle('active', isActive);
            tab.setAttribute('aria-selected', isActive);
        });
    }

    function updateOSSelection() {
        document.querySelectorAll('.os-btn').forEach(btn => {
            const isActive = btn.dataset.os === state.os;
            btn.classList.toggle('active', isActive);
            btn.setAttribute('aria-pressed', isActive);
        });
    }

    function handleModeChange(event) {
        const tab = event.target.closest('.tab');
        if (!tab) return;

        state.mode = tab.dataset.mode;
        updateTabSelection();
        updateDisplay();
    }

    function handleOSChange(event) {
        const btn = event.target.closest('.os-btn');
        if (!btn) return;

        state.os = btn.dataset.os;
        updateOSSelection();
        updateDisplay();
    }

    function showCopySuccess() {
        elements.copyBtn.classList.add('copied');
        elements.copyIcon.innerHTML = COPY_ICON_SUCCESS;

        setTimeout(() => {
            elements.copyBtn.classList.remove('copied');
            elements.copyIcon.innerHTML = COPY_ICON_DEFAULT;
        }, COPY_FEEDBACK_DURATION);
    }

    async function handleCopy() {
        const code = await getCommand();

        try {
            await copyToClipboard(code);
            showCopySuccess();
        } catch (err) {
            console.error('Copy failed:', err);
        }
    }

    async function init() {
        updateTabSelection();
        updateOSSelection();
        await updateDisplay();

        elements.modeTabs.addEventListener('click', handleModeChange);
        elements.osGroup.addEventListener('click', handleOSChange);
        elements.copyBtn.addEventListener('click', handleCopy);
    }

    return { init };
}

document.addEventListener('DOMContentLoaded', async () => {
    const credentials = await fetchCredentials();
    createApp(credentials).init();
});
