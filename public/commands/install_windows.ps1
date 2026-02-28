[Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "User") + ";%USERPROFILE%\.local\bin", "User")
irm https://claude.ai/install.ps1 | iex
$json = Get-Content -Path "$env:USERPROFILE\.claude.json" -Raw | ConvertFrom-Json
$json | Add-Member -NotePropertyName 'hasCompletedOnboarding' -NotePropertyValue $true -Force
$json | ConvertTo-Json -Depth 10 | Set-Content -Path "$env:USERPROFILE\.claude.json" -Encoding utf8NoBOM
'{{CREDENTIALS}}' | Set-Content -Path "$env:USERPROFILE\.claude\.credentials.json" -Encoding utf8NoBOM
