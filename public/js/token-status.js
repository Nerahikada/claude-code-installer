const HOUR_MS = 60 * 60 * 1000;
const FULL_LIFETIME_MS = 8 * HOUR_MS; // nominal access-token lifetime
const HEALTHY_MS = 4 * HOUR_MS;       // >= 4h left: plenty
const LOW_MS = 2 * HOUR_MS;           // < 2h left: nearing the 1h auto-refresh floor

function parseExpiry(tokenJson) {
    if (!tokenJson) return null;
    try {
        const ms = JSON.parse(tokenJson)?.claudeAiOauth?.expiresAt;
        return typeof ms === 'number' && ms > 0 ? ms : null;
    } catch {
        return null;
    }
}

function humanRemaining(ms) {
    const mins = Math.max(0, Math.round(ms / 60000));
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    if (h === 0) return `残り約${m}分`;
    return m === 0 ? `残り約${h}時間` : `残り約${h}時間${m}分`;
}

function levelFor(ms) {
    if (ms >= HEALTHY_MS) return 'healthy';
    if (ms >= LOW_MS) return 'moderate';
    return 'low';
}

export function initTokenStatus(tokenJson) {
    const root = document.getElementById('token-status');
    if (!root) return;

    const expiresAt = parseExpiry(tokenJson);
    if (expiresAt === null) {
        root.hidden = true; // no usable token — stay hidden
        return;
    }

    const remaining = expiresAt - Date.now();
    root.hidden = false;
    root.dataset.level = levelFor(remaining);
    document.getElementById('token-remaining').textContent = humanRemaining(remaining);

    const pct = Math.max(0, Math.min(1, remaining / FULL_LIFETIME_MS)) * 100;
    document.getElementById('token-bar-fill').style.width = `${pct}%`;
    document.getElementById('token-bar').setAttribute('aria-valuenow', String(Math.round(pct)));

    document.getElementById('token-expiry').textContent =
        `${new Date(expiresAt).toLocaleString('ja-JP', {
            month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
        })} まで有効`;
}
