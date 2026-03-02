$utf8NoBOM = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText("$env:USERPROFILE\.claude\.credentials.json", '{{CREDENTIALS}}', $utf8NoBOM)
