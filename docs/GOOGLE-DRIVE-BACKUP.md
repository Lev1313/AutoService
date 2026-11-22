# Резервные копии через Google Drive API

AutoService 0.5.20 создаёт проверенный снимок SQLite, шифрует его открытым ключом
`age` и передаёт через Google Drive API с помощью rclone. В «Настройках» отображаются
время последней подтверждённой загрузки, количество файлов в очереди и ошибка последней
попытки. Повторный запуск отправляет файлы из очереди. У каждого снимка отдельное имя.

## Подключение для учебного прогона

1. Скачайте [rclone для Windows](https://rclone.org/downloads/) и
   [age для Windows](https://github.com/FiloSottile/age/releases).
2. В Google Cloud включите Google Drive API и создайте OAuth-клиент типа
   **Desktop app** для проекта резервного копирования. Подключите выбранный Google-аккаунт
   через `rclone config`. Укажите собственный Client ID и Client Secret, тип **Google Drive**
   и право `drive.file`. Укажите путь к отдельному файлу через `--config`.
   Папку для копий создаст само подключение при первой загрузке.
   [Конфигурация Google Drive в rclone](https://rclone.org/drive/#making-your-own-client-id)
3. Создайте ключ командой `age-keygen -o recovery-key.txt`. Сохраните секретный файл
   отдельно от компьютера с базой и от облачной папки с копиями. В конфигурацию
   AutoService внесите открытый ключ `age1…`.
4. В отдельном профиле с учебной базой создайте рядом с базой каталог `drive-backup`
   и сохраните в нём `settings.json`. Укажите свои абсолютные пути и имя подключения.

```json
{
  "age": "C:/BackupTools/age.exe",
  "rclone": "C:/BackupTools/rclone.exe",
  "rclone_config": "C:/BackupTools/autoservice-rclone.conf",
  "remote": "autoservice:ФольцМастер — учебные копии",
  "recipient": "age1..."
}
```

Защитите файл конфигурации rclone с помощью **Set configuration password** в
`rclone config`. Для запуска по расписанию сохраните его пароль через PowerShell,
запущенный от того же пользователя Windows, что и задача. Введите пароль в защищённом
приглашении. Windows защищает сохранённый пароль для текущего пользователя этого ПК.
Команду выполняйте с `LOCALAPPDATA`, соответствующим выбранному профилю.
[Шифрование конфигурации rclone](https://rclone.org/docs/#configuration-encryption)

```powershell
$credential = [pscredential]::new('rclone', (Read-Host 'Пароль конфигурации rclone' -AsSecureString))
$credential | Export-Clixml -LiteralPath (Join-Path $env:LOCALAPPDATA 'AutoService/drive-backup/rclone-password.xml')
```

## Запуск и восстановление

1. Запустите из папки программы `powershell.exe -NoProfile -File .\run_drive_backup.ps1`
   с тем же профилем. В обычной установке это профиль текущего пользователя Windows.
   Подробный результат записывается рядом с базой в `drive-backup/status.json`.
2. Для рабочего режима укажите эту команду в Планировщике заданий Windows от того же
   пользователя. Например, запускайте каждый час в течение смены и при входе в Windows.
   Рабочая программа может быть закрыта. При отсутствии новых данных повторная копия
   не создаётся. При сбое готовый зашифрованный файл остаётся в `drive-backup/pending`
   до следующего запуска. Режим OAuth **Testing** для внешнего аккаунта ограничивает
   срок токена обновления семью днями. Проверьте режим проекта перед рабочим запуском.
   [Сроки токенов Google](https://developers.google.com/identity/protocols/oauth2#expiration)
3. На втором ПК скачайте файл `.sqlite3.age` из Google Диска и расшифруйте его
   командой `age -d -i recovery-key.txt -o recovered.sqlite3 имя-копии.sqlite3.age`.
   Откройте учебный профиль и выберите **Настройки → Восстановить из копии**.
   Сверьте последний ремонт, запись на приём, исходный TXT и PDF.
