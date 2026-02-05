curl -fsSL https://claude.ai/install.sh | bash

# 以下は動作するかどうか不明です。情報提供をお待ちしています。
sed -i '0,/{/s/{/{"hasCompletedOnboarding":true,/' ~/.claude.json
echo '{{CREDENTIALS}}' > ~/.claude/.credentials.json