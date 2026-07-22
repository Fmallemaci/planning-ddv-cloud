param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$ProtocolUrl
)

$ErrorActionPreference = "Stop"
$InstallDir = Join-Path $env:LOCALAPPDATA "PlanningDDVOutlookConnector"
$LogPath = Join-Path $InstallDir "connector.log"

function Write-ConnectorLog {
    param([string]$Stage, [string]$Message)
    if (!(Test-Path $InstallDir)) {
        New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    }
    $line = "{0} [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Stage, $Message
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

function Stop-WithConnectorError {
    param([string]$Stage, [System.Management.Automation.ErrorRecord]$ErrorRecord)
    $message = $ErrorRecord.Exception.Message
    $position = $ErrorRecord.InvocationInfo.PositionMessage
    Write-ConnectorLog $Stage $message
    Write-ConnectorLog $Stage $position
    Write-Host ""
    Write-Host "Planning DDV Outlook Connector - ERROR" -ForegroundColor Red
    Write-Host "Etapa: $Stage"
    Write-Host "Mensaje: $message"
    Write-Host "Archivo y linea:"
    Write-Host $position
    Write-Host "Log: $LogPath"
    Write-Host ""
    Read-Host "Presione Enter para cerrar"
    exit 1
}

function Get-QueryValue {
    param([string]$Url, [string]$Name)
    Add-Type -AssemblyName System.Web
    $uri = [Uri]$Url
    $query = [System.Web.HttpUtility]::ParseQueryString($uri.Query)
    return $query[$Name]
}

function Assert-NotBlank {
    param([string]$Name, [string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "$Name vacio o no informado."
    }
}

function Write-AttachmentFile {
    param([object]$Attachment, [string]$Folder)
    $safeName = [IO.Path]::GetFileName([string]$Attachment.name)
    if ([string]::IsNullOrWhiteSpace($safeName)) {
        $safeName = [Guid]::NewGuid().ToString("N")
    }
    $path = Join-Path $Folder $safeName
    [IO.File]::WriteAllBytes($path, [Convert]::FromBase64String([string]$Attachment.content_base64))
    if (!(Test-Path -LiteralPath $path)) {
        throw "No se pudo crear adjunto local: $path"
    }
    Write-ConnectorLog "Adjunto" $path
    return $path
}

$tempDir = $null
try {
    Write-ConnectorLog "Inicio" "URL recibida: $ProtocolUrl"
    $BaseUrl = $env:PLANNING_DDV_BASE_URL
    if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
        $BaseUrl = "https://planning-ddv-usuarios-prueba.onrender.com"
    }
    $BaseUrl = $BaseUrl.TrimEnd("/")

    $token = Get-QueryValue -Url $ProtocolUrl -Name "token"
    Assert-NotBlank "Token" $token

    $packageUrl = "$BaseUrl/api/mail/package/$token"
    Write-ConnectorLog "Descarga paquete" $packageUrl
    $package = Invoke-RestMethod -Method GET -Uri $packageUrl -TimeoutSec 45

    Assert-NotBlank "Destinatario" ([string]$package.to)
    Assert-NotBlank "Asunto" ([string]$package.subject)
    Assert-NotBlank "Cuerpo HTML" ([string]$package.html_body)

    $tempDir = Join-Path ([IO.Path]::GetTempPath()) ("planningddv_mail_" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
    Write-ConnectorLog "Temporal" $tempDir

    $outlook = $null
    try {
        $outlook = New-Object -ComObject Outlook.Application
    } catch {
        throw "No se pudo crear Outlook.Application. Verifique que Outlook clasico de escritorio este instalado. El Nuevo Outlook no expone Outlook COM."
    }
    if ($null -eq $outlook) {
        throw "Outlook.Application devolvio null."
    }

    $mail = $outlook.CreateItem(0)
    $mail.To = [string]$package.to
    $mail.CC = [string]$package.cc
    $mail.Subject = [string]$package.subject

    $hasPdf = $false
    foreach ($attachment in @($package.attachments)) {
        $filePath = Write-AttachmentFile -Attachment $attachment -Folder $tempDir
        if ($filePath.ToLowerInvariant().EndsWith(".pdf")) {
            $hasPdf = $true
        }
        $item = $mail.Attachments.Add($filePath)
        if ($attachment.inline -and -not [string]::IsNullOrWhiteSpace([string]$attachment.cid)) {
            $item.PropertyAccessor.SetProperty(
                "http://schemas.microsoft.com/mapi/proptag/0x3712001F",
                [string]$attachment.cid
            )
        }
    }
    if (-not $hasPdf) {
        throw "El paquete no contiene PDF adjunto."
    }

    $mail.Display()
    $signature = [string]$mail.HTMLBody
    $mail.HTMLBody = [string]$package.html_body + "<br>" + $signature
    Write-ConnectorLog "Outlook" "Borrador creado correctamente."
} catch {
    Stop-WithConnectorError "Fallo general" $_
} finally {
    if ($tempDir -and (Test-Path -LiteralPath $tempDir)) {
        Start-Sleep -Milliseconds 500
        Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
