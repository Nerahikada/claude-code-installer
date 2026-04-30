const commandCache = new Map();

export async function fetchCommand(mode, os) {
    const key = `${mode}_${os}`;
    if (commandCache.has(key)) return commandCache.get(key);

    const ext = os === 'windows' ? 'ps1' : 'sh';
    try {
        const r = await fetch(`commands/${mode}_${os}.${ext}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const text = (await r.text()).trim();
        commandCache.set(key, text);
        return text;
    } catch (err) {
        console.error(`Failed to fetch command ${key}:`, err);
        return '# コマンドの読み込みに失敗しました';
    }
}

export async function fetchToken(provider = 'claude') {
    try {
        const r = await fetch(`/api/tokens/${provider}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return (await r.text()).trim();
    } catch (err) {
        console.error(`Failed to fetch token (${provider}):`, err);
        return null;
    }
}
