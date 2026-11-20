#ifndef AppVersion
  #error AppVersion must be specified
#endif
#ifndef BundleDir
  #error BundleDir must be specified
#endif
#ifndef ReleaseDir
  #error ReleaseDir must be specified
#endif

[Setup]
AppId={{A3553935-C9A5-40F9-9266-ABDC9A3C44CE}
AppName=AutoService
AppVersion={#AppVersion}
AppVerName=AutoService {#AppVersion}
DefaultDirName={localappdata}\Programs\AutoService
DefaultGroupName=AutoService
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#ReleaseDir}
OutputBaseFilename=AutoService-{#AppVersion}-Windows-x64-Setup
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
SetupIconFile={#BundleDir}\_internal\app\assets\autoservice.ico
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\AutoService.exe
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; Flags: checkedonce

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: files; Name: "{app}\Показ механику.cmd"
Type: files; Name: "{group}\Показ механику (отдельные данные).lnk"

[Icons]
Name: "{group}\AutoService"; Filename: "{app}\AutoService.exe"
Name: "{group}\Демонстрационный режим"; Filename: "{app}\AutoService.exe"; Parameters: "--demo"
Name: "{group}\Инструкция"; Filename: "{app}\Инструкция.txt"
Name: "{autodesktop}\AutoService"; Filename: "{app}\AutoService.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AutoService.exe"; Description: "Открыть AutoService"; Flags: nowait postinstall skipifsilent

; Данные в {localappdata}\AutoService не принадлежат установщику:
; ни обновление, ни удаление программы их не очищает.
