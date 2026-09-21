curl -fsSL https://claude.ai/install.sh | bash
curl -fsSL {{ORIGIN}}/policy/apply.py | python3 -
(umask 077; echo '{{TOKEN}}' > ~/.claude/.credentials.json)