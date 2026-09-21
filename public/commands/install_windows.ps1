[Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "User") + ";%USERPROFILE%\.local\bin", "User")
irm https://claude.ai/install.ps1 | iex
irm {{ORIGIN}}/policy/apply.ps1 | iex
$utf8NoBOM = New-Object System.Text.UTF8Encoding $false; [System.IO.File]::WriteAllText("$env:USERPROFILE\.claude\.credentials.json", '{{TOKEN}}', $utf8NoBOM)