const commandCache = new Map();

export async function fetchCommand(mode, os) {
    const cacheKey = `${mode}_${os}`;
    if (commandCache.has(cacheKey)) {
        return commandCache.get(cacheKey);
    }

    const ext = os === 'windows' ? 'ps1' : 'sh';
    const filename = `commands/${mode}_${os}.${ext}`;

    try {
        const response = await fetch(filename);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        const text = await response.text();
        commandCache.set(cacheKey, text.trim());
        return text.trim();
    } catch (err) {
        console.error(`Failed to fetch command ${filename}:`, err);
        return '# コマンドの読み込みに失敗しました';
    }
}

export async function fetchCredentials(provider = 'claude', maxRetries = 4) {
    for (let attempt = 0; attempt < maxRetries; attempt++) {
        try {
            const response = await fetch(`/api/credentials/${provider}`);
            if (response.status === 503) {
                const delay = Number(response.headers.get('Retry-After') || '1') * 1000;
                await new Promise(r => setTimeout(r, delay));
                continue;
            }
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            return (await response.text()).trim();
        } catch (err) {
            console.error(`Failed to fetch credentials (${provider}):`, err);
            return null;
        }
    }
    console.error(`Failed to fetch credentials (${provider}): max retries exceeded`);
    return null;
}
