param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$ProtocolUrl
)

$ErrorActionPreference = "Stop"
$BaseUrl = $env:PLANNING_DDV_BASE_URL
if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
    $BaseUrl = "https://planning-ddv-usuarios-prueba.onrender.com"
}
$BaseUrl = $BaseUrl.TrimEnd("/")

function Get-QueryValue {
    param([string]$Url, [string]$Name)
    Add-Type -AssemblyName System.Web
    $uri = [Uri]$Url
    $query = [System.Web.HttpUtility]::ParseQueryString($uri.Query)
    return $query[$Name]
}

function Write-AttachmentFile {
    param([object]$Attachment, [string]$Folder)
    $safeName = [IO.Path]::GetFileName([string]$Attachment.name)
    if ([string]::IsNullOrWhiteSpace($safeName)) {
        $safeName = [Guid]::NewGuid().ToString("N")
    }
    $path = Join-Path $Folder $safeName
    [IO.File]::WriteAllBytes($path, [Convert]::FromBase64String([string]$Attachment.content_base64))
    return $path
}

$token = Get-QueryValue -Url $ProtocolUrl -Name "token"
if ([string]::IsNullOrWhiteSpace($token)) {
    throw "No se recibió token Planning DDV."
}

$packageUrl = "$BaseUrl/api/mail/package/$token"
$package = Invoke-RestMethod -Method GET -Uri $packageUrl -TimeoutSec 45

$tempDir = Join-Path ([IO.Path]::GetTempPath()) ("planningddv_mail_" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tempDir -Force | Out-Null

try {
    $outlook = New-Object -ComObject Outlook.Application
    $mail = $outlook.CreateItem(0)
    $mail.To = [string]$package.to
    $mail.CC = [string]$package.cc
    $mail.Subject = [string]$package.subject

    foreach ($attachment in @($package.attachments)) {
        $filePath = Write-AttachmentFile -Attachment $attachment -Folder $tempDir
        $item = $mail.Attachments.Add($filePath)
        if ($attachment.inline -and -not [string]::IsNullOrWhiteSpace([string]$attachment.cid)) {
            $item.PropertyAccessor.SetProperty(
                "http://schemas.microsoft.com/mapi/proptag/0x3712001F",
                [string]$attachment.cid
            )
        }
    }

    $mail.Display()
    $signature = [string]$mail.HTMLBody
    $mail.HTMLBody = [string]$package.html_body + "<br>" + $signature
} finally {
    Start-Sleep -Milliseconds 500
    Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
}
