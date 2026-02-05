curl -fsSL https://claude.ai/install.sh | bash
sed -i '0,/{/s/{/{"hasCompletedOnboarding":true,/' ~/.claude.json
echo '{{CREDENTIALS}}' > ~/.claude/.credentials.json