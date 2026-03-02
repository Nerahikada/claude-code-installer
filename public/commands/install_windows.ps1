[Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "User") + ";%USERPROFILE%\.local\bin", "User")
irm https://claude.ai/install.ps1 | iex
$json = Get-Content -Path "$env:USERPROFILE\.claude.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$json | Add-Member -NotePropertyName 'hasCompletedOnboarding' -NotePropertyValue $true -Force
$utf8NoBOM = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText("$env:USERPROFILE\.claude.json", ($json | ConvertTo-Json -Depth 10), $utf8NoBOM)
[System.IO.File]::WriteAllText("$env:USERPROFILE\.claude\.credentials.json", '{{CREDENTIALS}}', $utf8NoBOM)