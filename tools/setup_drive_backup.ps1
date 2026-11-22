param([string]$ClientJsonPath, [string]$DailyAt = '19:00', [switch]$CheckOnly)

$ErrorActionPreference = 'Stop'
$taskName = 'FoltzMaster Google Drive Backup'
$powershell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$localData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
$data = Join-Path $localData 'AutoService'
$database = Join-Path $data 'autoservice.sqlite3'
$drive = Join-Path $data 'drive-backup'
$install = Join-Path $localData 'FoltzMasterBackup\0.5.20'
$exe = Join-Path $install 'AutoService-0.5.20\AutoService.exe'
$config = Join-Path $drive 'work-rclone.conf'
$passwordFile = Join-Path $drive 'rclone-password.xml'
$settingsFile = Join-Path $drive 'settings.json'
$statusFile = Join-Path $drive 'status.json'
$remote = 'autoservice:FoltzMaster-work-backups'
$metadata = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'setup-profile.json') -Raw | ConvertFrom-Json
$encoding = New-Object System.Text.UTF8Encoding($false)
$recipient = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'recipient.txt')).Trim()

function Select-ClientJson {
    Add-Type -AssemblyName System.Windows.Forms
    $picker = New-Object System.Windows.Forms.OpenFileDialog
    try {
        $picker.Title = 'Выберите JSON личного OAuth-клиента Google'
        $picker.InitialDirectory = Join-Path $env:USERPROFILE 'Downloads'
        $picker.Filter = 'OAuth JSON (client_secret*.json)|client_secret*.json|JSON (*.json)|*.json'
        if ($picker.ShowDialog() -eq 'OK') { return $picker.FileName }
    } finally { $picker.Dispose() }
}

function Set-RcloneConnection($client, $rclone) {
    if (-not (Test-Path -LiteralPath $config)) {
        if (-not (Test-Path -LiteralPath $passwordFile)) {
            $bytes = New-Object byte[] 32
            $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
            try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
            $secret = ConvertTo-SecureString ([Convert]::ToBase64String($bytes)) -AsPlainText -Force
            [pscredential]::new('rclone', $secret) | Export-Clixml -LiteralPath $passwordFile -NoClobber
        }
        $temporary = Join-Path ([IO.Path]::GetTempPath()) ('foltzmaster-' + [guid]::NewGuid() + '.conf')
        try {
            [IO.File]::WriteAllLines($temporary, @(
                '[autoservice]', 'type = drive', 'scope = drive.file',
                ('client_id = ' + $client.client_id), ('client_secret = ' + $client.client_secret)
            ), $encoding)
            $env:FOLTZMASTER_PASSWORD_FILE = $passwordFile
            $reader = '(Import-Clixml -LiteralPath $env:FOLTZMASTER_PASSWORD_FILE).GetNetworkCredential().Password'
            $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($reader))
            $readCommand = "powershell.exe -NoProfile -NonInteractive -EncodedCommand $encoded"
            & $rclone config encryption set --config $temporary --ask-password=false --password-command $readCommand
            if ($LASTEXITCODE -ne 0 -or -not (Select-String -LiteralPath $temporary -SimpleMatch 'RCLONE_ENCRYPT_V0:' -Quiet)) {
                throw 'Не удалось зашифровать конфигурацию rclone.'
            }
            [IO.File]::Copy($temporary, $config, $false)
        } finally {
            if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
            Remove-Item Env:FOLTZMASTER_PASSWORD_FILE -ErrorAction SilentlyContinue
        }
    }
    $credential = Import-Clixml -LiteralPath $passwordFile
    $env:RCLONE_CONFIG_PASS = $credential.GetNetworkCredential().Password
    try {
        @('y','n') | & $rclone config reconnect autoservice: --config $config --ask-password=false
        if ($LASTEXITCODE -ne 0) { throw 'Google не завершил авторизацию. Можно повторить настройку.' }
    } finally { Remove-Item Env:RCLONE_CONFIG_PASS -ErrorAction SilentlyContinue }
}

function New-BackupTaskArguments {
    # Задача читает пароль под тем же пользователем Windows.
    $command = @'
$ErrorActionPreference='Stop'
$env:LOCALAPPDATA=[Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
$folder=Join-Path $env:LOCALAPPDATA 'AutoService\drive-backup'
$env:RCLONE_CONFIG_PASS=(Import-Clixml -LiteralPath (Join-Path $folder 'rclone-password.xml')).GetNetworkCredential().Password
try {
    $exe=Join-Path $env:LOCALAPPDATA 'FoltzMasterBackup\0.5.20\AutoService-0.5.20\AutoService.exe'
    $child=Start-Process -FilePath $exe -ArgumentList '--drive-backup' -WindowStyle Hidden -Wait -PassThru
    exit $child.ExitCode
} finally { Remove-Item Env:RCLONE_CONFIG_PASS -ErrorAction SilentlyContinue }
'@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    return "-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand $encoded"
}

if ($recipient -notmatch '^age1[0-9a-z]+$' -or $metadata.project -notmatch '^[a-z0-9-]+$') {
    throw 'Неверный открытый ключ или идентификатор проекта в комплекте.'
}
foreach ($relative in 'bin\age.exe', 'bin\rclone.exe', 'AutoService-0.5.20\AutoService.exe') {
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $relative) -PathType Leaf)) {
        throw "В комплекте отсутствует $relative. Распакуйте архив целиком."
    }
}
$time = [TimeSpan]::ParseExact($DailyAt, 'hh\:mm', [Globalization.CultureInfo]::InvariantCulture)
$taskArguments = New-BackupTaskArguments
if ($CheckOnly) { Write-Output 'Комплект читается. Настройка не запускалась.'; exit 0 }
if (-not (Test-Path -LiteralPath $database -PathType Leaf) -or (Get-Item -LiteralPath $database).Length -lt 100) {
    throw "В профиле пользователя Windows нет рабочей базы: $database"
}
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { throw "Задание $taskName уже существует." }
if (-not $ClientJsonPath) { $ClientJsonPath = Select-ClientJson }
if (-not $ClientJsonPath) { Write-Output 'Настройка отменена.'; exit 0 }
$client = (Get-Content -LiteralPath $ClientJsonPath -Raw | ConvertFrom-Json).installed
if ($client.project_id -ne $metadata.project -or $client.client_id -notmatch '^[A-Za-z0-9._-]+\.apps\.googleusercontent\.com$' -or
    $client.client_secret -notmatch '^[A-Za-z0-9_-]+$') { throw 'Выберите JSON рабочего проекта Google Personal.' }
if (Test-Path -LiteralPath $settingsFile) {
    $existing = Get-Content -LiteralPath $settingsFile -Raw | ConvertFrom-Json
    if ($existing.remote -ne $remote -or $existing.recipient -ne $recipient -or $existing.rclone_config -ne $config) {
        throw 'В профиле обнаружена другая настройка резервного копирования.'
    }
}
New-Item -ItemType Directory -Path $drive,$install -Force | Out-Null
foreach ($folder in 'AutoService-0.5.20','bin') {
    if (-not (Test-Path -LiteralPath (Join-Path $install $folder))) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $folder) -Destination $install -Recurse
    }
}
$age = Join-Path $install 'bin\age.exe'
$rclone = Join-Path $install 'bin\rclone.exe'
foreach ($file in $exe,$age,$rclone) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Установка неполная: $file" }
}
Write-Host 'В открывшемся окне Google выберите ваш личный аккаунт и подтвердите доступ.'
Set-RcloneConnection $client $rclone

Write-Host "Рабочая база: $database"
Write-Host 'Папка на вашем Google Диске: FoltzMaster-work-backups'
Write-Host "Запуск при входе в Windows и ежедневно в $DailyAt по времени ПК."
if ((Read-Host 'Чтобы отправить зашифрованную рабочую копию и включить расписание, введите YES') -cne 'YES') {
    Write-Output 'Подключение подготовлено. Отправка и расписание не включены.'
    exit 0
}
if (-not (Test-Path -LiteralPath $settingsFile)) {
    $settings = @{ age=$age; rclone=$rclone; rclone_config=$config; remote=$remote; recipient=$recipient }
    [IO.File]::WriteAllText($settingsFile, ($settings | ConvertTo-Json), $encoding)
}
$job = Start-Process -FilePath $powershell -ArgumentList $taskArguments -WindowStyle Hidden -Wait -PassThru
if (-not (Test-Path -LiteralPath $statusFile -PathType Leaf)) { throw 'Первый запуск не записал результат.' }
$status = Get-Content -LiteralPath $statusFile -Raw | ConvertFrom-Json
if ($job.ExitCode -ne 0 -or $status.error -or $status.pending -ne 0 -or -not $status.confirmed_at) {
    throw "Первая копия не подтверждена. Расписание не создано. $($status.error)"
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $powershell -Argument $taskArguments
$triggers = @((New-ScheduledTaskTrigger -Daily -At ([DateTime]::Today + $time)), (New-ScheduledTaskTrigger -AtLogOn -User $identity))
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$options = @{ StartWhenAvailable=$true; MultipleInstances='IgnoreNew'; AllowStartIfOnBatteries=$true
    DontStopIfGoingOnBatteries=$true; RestartCount=3; RestartInterval=(New-TimeSpan -Minutes 30) }
$taskSettings = New-ScheduledTaskSettingsSet @options
$task = @{ TaskName=$taskName; Action=$action; Trigger=$triggers; Principal=$principal; Settings=$taskSettings }
Register-ScheduledTask @task | Out-Null
$checked = Get-ScheduledTask -TaskName $taskName
if ($checked.State -eq 'Disabled' -or $checked.Triggers.Count -ne 2) { throw 'Не удалось проверить расписание.' }
Write-Output "Первая копия подтверждена. Задание $taskName включено."
