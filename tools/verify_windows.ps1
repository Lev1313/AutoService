param(
    [Parameter(Mandatory = $true)][string]$ReleaseDirectory,
    [Parameter(Mandatory = $true)][string]$TestDirectory,
    [string]$TestInstaller = '',
    [string]$TestApplicationId = ''
)
$ErrorActionPreference = 'Stop'
$release = (Resolve-Path -LiteralPath $ReleaseDirectory).Path
$root = [IO.Path]::GetFullPath($TestDirectory)
if (Test-Path -LiteralPath $root) { throw 'Test directory must be new' }
$installer = @(Get-ChildItem -LiteralPath $release -Filter '*-Setup.exe')
if ($installer.Count -ne 1) { throw 'Exactly one installer is required' }
$productionKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{A3553935-C9A5-40F9-9266-ABDC9A3C44CE}_is1'
$productionState = if (Test-Path -LiteralPath $productionKey) { (Get-ItemProperty -LiteralPath $productionKey | Select-Object * -ExcludeProperty PS* | ConvertTo-Json -Compress) } else { '' }

$registryKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{A3553935-C9A5-40F9-9266-ABDC9A3C44CE}_is1'
if ($TestInstaller -or $TestApplicationId) {
    if (-not $TestInstaller -or $TestApplicationId -notmatch '^\{[0-9A-Fa-f-]{36}\}$' -or $TestApplicationId -eq '{A3553935-C9A5-40F9-9266-ABDC9A3C44CE}') {
        throw 'Isolated verification requires a different valid AppId and its installer'
    }
    $installer = @(Get-Item -LiteralPath $TestInstaller)
    $registryKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\' + $TestApplicationId + '_is1'
}
if (Test-Path -LiteralPath $registryKey) { throw 'An AutoService installation already exists: installation test stopped' }
New-Item -ItemType Directory -Path $root | Out-Null
$oldEnvironment = @{}
foreach ($name in @('LOCALAPPDATA','PATH','PYTHONHOME','PYTHONPATH','QT_PLUGIN_PATH','QT_QPA_PLATFORM','QML2_IMPORT_PATH')) {
    $oldEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
$env:LOCALAPPDATA = Join-Path $root 'local-data'
$env:PATH = "$env:WINDIR\System32;$env:WINDIR"
$env:QT_QPA_PLATFORM = 'windows'
foreach ($name in @('PYTHONHOME','PYTHONPATH','QT_PLUGIN_PATH','QML2_IMPORT_PATH')) {
    [Environment]::SetEnvironmentVariable($name, $null, 'Process')
}

function Run-Checked([string]$File, [string]$Arguments) {
    $process = Start-Process -FilePath $File -ArgumentList $Arguments -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(45000)) {
        $process.Kill()
        throw "Timed out: $File"
    }
    if ($process.ExitCode -ne 0) { throw "Exit $($process.ExitCode): $File" }
}

function Check-NormalLaunch([string]$File, [string]$Name) {
    $process = Start-Process -FilePath $File -WindowStyle Normal -PassThru
    try {
        for ($iteration = 0; $iteration -lt 50; $iteration++) {
            Start-Sleep -Milliseconds 200
            $process.Refresh()
            if ($process.HasExited) { throw 'Normal launch exited early' }
            if ($process.MainWindowTitle -like 'АвтоСервис*') { break }
        }
        if ($process.MainWindowTitle -notlike 'АвтоСервис*') { throw 'Main window did not open' }
        $modulePaths = @($process.Modules | ForEach-Object { $_.FileName })
        $modulePaths | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root "$Name-modules.json") -Encoding utf8
        $internal = Join-Path ([IO.Path]::GetDirectoryName($File)) '_internal'
        $runtimeModules = @($modulePaths | Where-Object { [IO.Path]::GetFileName($_) -match '^(python|Qt6|pyside6|shiboken6)' })
        if ($runtimeModules.Count -lt 7) { throw 'Runtime module evidence is incomplete' }
        foreach ($module in $runtimeModules) {
            if (-not $module.StartsWith($internal + '\', [StringComparison]::OrdinalIgnoreCase)) {
                throw "External Python/Qt module loaded: $module"
            }
        }
        if ($Name -eq 'portable') {
            $second = Start-Process -FilePath $File -WindowStyle Normal -PassThru
            try {
                for ($iteration = 0; $iteration -lt 50; $iteration++) {
                    Start-Sleep -Milliseconds 200
                    $second.Refresh()
                    if ($second.HasExited) { throw 'Second launch exited without visible explanation' }
                    if ($second.MainWindowTitle -eq 'Не удалось открыть AutoService') { break }
                }
                if ($second.MainWindowTitle -ne 'Не удалось открыть AutoService') { throw 'Second instance was not blocked' }
                if (-not $second.CloseMainWindow()) { throw 'Cannot close second-instance message' }
                if (-not $second.WaitForExit(10000) -or $second.ExitCode -ne 1) { throw 'Second instance did not exit safely' }
            }
            finally { if (-not $second.HasExited) { $second.Kill() } }
        }
        if (-not $process.CloseMainWindow()) { throw 'Cannot close normal window' }
        if (-not $process.WaitForExit(10000)) { throw 'Normal window did not close' }
        if ($process.ExitCode -ne 0) { throw 'Normal exit failed' }
    }
    finally {
        if (-not $process.HasExited) { $process.Kill() }
    }
}

try {
    $portable = Join-Path $release 'dist\AutoService\AutoService.exe'
    $scenario = Join-Path $root 'frozen-scenario'
    Run-Checked $portable ('--self-test "' + $scenario + '"')
    Run-Checked $portable ('--self-test-reopen "' + $scenario + '"')
    $report = Get-Content -LiteralPath (Join-Path $scenario 'release-check.json') -Raw | ConvertFrom-Json
    if ($report.stage -ne 'passed' -or -not $report.frozen) { throw 'Frozen scenario failed' }
    Check-NormalLaunch $portable 'portable'
    $database = Join-Path $env:LOCALAPPDATA 'AutoService\autoservice.sqlite3'
    $originalHash = (Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash
    $installed = Join-Path $root 'Установка AutoService'
    $setupArguments = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOICONS /TASKS="" /DIR="' + $installed + '"'
    Run-Checked $installer[0].FullName ($setupArguments + ' /LOG="' + (Join-Path $root 'install.log') + '"')
    if ((Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash -ne $originalHash) { throw 'Install modified data' }
    $installedExe = Join-Path $installed 'AutoService.exe'
    Check-NormalLaunch $installedExe 'installed'
    $installedScenario = Join-Path $root 'installed-scenario'
    Run-Checked $installedExe ('--self-test "' + $installedScenario + '"')
    Run-Checked $installedExe ('--self-test-reopen "' + $installedScenario + '"')
    $originalHash = (Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash
    Run-Checked $installer[0].FullName ($setupArguments + ' /LOG="' + (Join-Path $root 'update.log') + '"')
    if ((Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash -ne $originalHash) { throw 'Update modified data' }
    Check-NormalLaunch $installedExe 'updated'
    Run-Checked $installedExe ('--self-test-reopen "' + $installedScenario + '"')
    $originalHash = (Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash
    $uninstaller = Join-Path $installed 'unins000.exe'
    if (-not ([IO.Path]::GetFullPath($uninstaller)).StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Uninstaller is outside test directory'
    }
    Run-Checked $uninstaller '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART'
    if ((Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash -ne $originalHash) { throw 'Uninstall modified data' }
    if (Test-Path -LiteralPath $installedExe) { throw 'Uninstall did not remove program' }
    if (Test-Path -LiteralPath $registryKey) { throw 'Test uninstall registry entry remains' }
    [ordered]@{stage='passed';frozen_scenario=$true;normal_launch=$true;installation=$true;
        reinstall=$true;uninstall=$true;data_sha256=$originalHash;data_preserved=$true;
        second_instance_blocked=$true;external_python_qt_loaded=$false;clean_computer_tested=$false} |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'windows-check.json') -Encoding utf8
    if ($TestApplicationId) {
        $productionAfter = if (Test-Path -LiteralPath $productionKey) { (Get-ItemProperty -LiteralPath $productionKey | Select-Object * -ExcludeProperty PS* | ConvertTo-Json -Compress) } else { '' }
        if ($productionAfter -ne $productionState) { throw 'Production installation registry changed' }
    }
    Write-Output "Windows release check passed: $root"
}
finally {
    foreach ($name in $oldEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $oldEnvironment[$name], 'Process')
    }
}
