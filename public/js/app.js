import { fetchCommand, fetchToken } from './api.js';
import { getInitialOS, getInitialMode, getLanguageForOS, saveSelection, copyToClipboard } from './utils.js';

const TOKEN_PLACEHOLDER = '{{TOKEN}}';
const COPY_ICON_DEFAULT = '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>';
const COPY_ICON_SUCCESS = '<polyline points="20 6 9 17 4 12"></polyline>';
const COPY_FEEDBACK_DURATION = 2000;

function createApp(token) {
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
        if (token) {
            command = command.replace(TOKEN_PLACEHOLDER, token);
        }
        return command;
    }

    async function updateDisplay() {
        saveSelection(state.mode, state.os);

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
        try {
            await copyToClipboard(elements.codeDisplay.textContent);
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
    const token = await fetchToken();
    await createApp(token).init();
});
