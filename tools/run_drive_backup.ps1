param(
    [string]$Executable = (Join-Path $PSScriptRoot 'AutoService.exe')
)

$ErrorActionPreference = 'Stop'
$credentialFile = Join-Path $env:LOCALAPPDATA 'AutoService\drive-backup\rclone-password.xml'
if (Test-Path -LiteralPath $credentialFile) {
    $credential = Import-Clixml -LiteralPath $credentialFile
    $env:RCLONE_CONFIG_PASS = $credential.GetNetworkCredential().Password
}

try {
    $process = Start-Process -FilePath $Executable -ArgumentList '--drive-backup' `
        -WindowStyle Hidden -Wait -PassThru
    exit $process.ExitCode
} finally {
    Remove-Item Env:RCLONE_CONFIG_PASS -ErrorAction SilentlyContinue
}
