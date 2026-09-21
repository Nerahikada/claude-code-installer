& {
    Set-StrictMode -Version 3.0
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'

    # Kept in sync with GITHUB_RAW_BASE in serv.py, which rewrites this prefix.
    $PolicyUrl = 'https://raw.githubusercontent.com/Nerahikada/claude-code-installer/master/policy/policy.json'

    $Utf8NoBom = New-Object System.Text.UTF8Encoding $false

    function Test-JsonObject($Value) {
        if ($null -eq $Value) { return $false }
        if ($Value -is [string] -or $Value -is [ValueType] -or $Value -is [array]) { return $false }
        return $true
    }

    function Read-Policy([string]$Source) {
        if ($PSVersionTable.PSEdition -eq 'Desktop') {
            $tls12 = [Net.SecurityProtocolType]::Tls12
            if (([Net.ServicePointManager]::SecurityProtocol -band $tls12) -ne $tls12) {
                [Net.ServicePointManager]::SecurityProtocol =
                    [Net.ServicePointManager]::SecurityProtocol -bor $tls12
            }
        }
        $content = (Invoke-WebRequest -UseBasicParsing -Uri $Source -TimeoutSec 30 -UserAgent 'claude-code-installer/apply.ps1').Content
        # 5.1 hands back a Byte[] whenever it does not recognise the content type as text.
        if ($content -is [byte[]]) {
            $raw = [Text.Encoding]::UTF8.GetString($content)
        } else {
            $raw = [string]$content
        }
        $policy = $raw | ConvertFrom-Json
        if (-not (Test-JsonObject $policy)) {
            throw "$Source does not hold a JSON object"
        }
        foreach ($entry in $policy.PSObject.Properties) {
            if (-not (Test-JsonObject $entry.Value)) {
                throw "$Source maps ""$($entry.Name)"" to something other than a JSON object"
            }
        }
        return $policy
    }

    function Resolve-PolicyPath([string]$Path) {
        if ($Path.StartsWith('~/') -or $Path.StartsWith('~\')) {
            return Join-Path $env:USERPROFILE $Path.Substring(2)
        }
        return $Path
    }

    function Read-Config([string]$Path) {
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
            return New-Object PSObject
        }
        $text = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
        if ([string]::IsNullOrWhiteSpace($text)) {
            return New-Object PSObject
        }
        $doc = $text | ConvertFrom-Json
        if (-not (Test-JsonObject $doc)) {
            throw "$Path does not hold a JSON object"
        }
        return $doc
    }

    function Merge-Policy($Base, $Patch) {
        foreach ($prop in $Patch.PSObject.Properties) {
            $existing = $Base.PSObject.Properties[$prop.Name]
            if ($existing -and (Test-JsonObject $prop.Value) -and (Test-JsonObject $existing.Value)) {
                Merge-Policy $existing.Value $prop.Value
            } elseif ($existing -is [Management.Automation.PSNoteProperty]) {
                $existing.Value = $prop.Value
            } else {
                $Base | Add-Member -NotePropertyName $prop.Name -NotePropertyValue $prop.Value -Force
            }
        }
    }

    function Write-Config([string]$Path, [string]$Json) {
        $parent = Split-Path -Parent $Path
        if ($parent -and -not (Test-Path -LiteralPath $parent)) {
            New-Item -ItemType Directory -Force -Path $parent | Out-Null
        }
        $tmp = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
        try {
            [IO.File]::WriteAllText($tmp, $Json, $Utf8NoBom)
            Move-Item -LiteralPath $tmp -Destination $Path -Force
        } catch {
            Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
            throw
        }
    }

    try {
        if ($PSVersionTable.PSVersion.Major -lt 5) {
            throw "Windows PowerShell 5.1 or later is required (found $($PSVersionTable.PSVersion))"
        }

        $policy = Read-Policy $PolicyUrl
        Write-Host "policy from $PolicyUrl"

        foreach ($entry in $policy.PSObject.Properties) {
            $path = Resolve-PolicyPath $entry.Name
            $doc = Read-Config $path
            $before = ConvertTo-Json $doc -Depth 100 -Compress

            Merge-Policy $doc $entry.Value
            $after = ConvertTo-Json $doc -Depth 100 -Compress

            if ($after -ceq $before) {
                Write-Host "  ${path}: already current"
                continue
            }
            Write-Config $path $after
            Write-Host "  ${path}: updated"
        }
    } catch {
        throw "policy not applied: $($_.Exception.Message)"
    }
}
