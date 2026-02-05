[Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "User") + ";%USERPROFILE%\\.local\\bin", "User")
irm https://claude.ai/install.ps1 | iex
# TODO: skip first onboarding
'{{CREDENTIALS}}' | Out-File -FilePath "$env:USERPROFILE\.claude\.credentials.json" -Encoding utf8