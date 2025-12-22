; Inno Setup script для упаковки dist\BarTenderGUI в установщик.
#define MyAppName "BarTenderGUI"
#define MyAppVersion "3.0"
#define MyAppPublisher "AutoPrint"
#define MyAppExeName "BarTenderGUI.exe"

[Setup]
AppId={{B9F5C8A4-4F63-4B43-B93B-FA2C1E25B111}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=BarTenderGUI_Setup
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
DirExistsWarning=no

[Files]
Source: "..\dist\BarTenderGUI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Кладём в установку исходные файлы-ресурсы (если они лежат рядом с корнем проекта)
Source: "..\product_catalog.xlsx"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist
Source: "..\preview.png"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist
Source: "..\tmp_batch.csv"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist
Source: "..\config\stats.json"; DestDir: "{app}\config"; Flags: ignoreversion onlyifdoesntexist
Source: "..\config\config.json"; DestDir: "{app}\config"; Flags: ignoreversion onlyifdoesntexist
; Шаблоны BarTender храним в подпапке templates
Source: "..\templates\*"; DestDir: "{app}\templates"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\BarTender GUI"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\BarTender GUI"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Создать значок на рабочем столе"; GroupDescription: "Дополнительно:"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить {#MyAppName}"; Flags: nowait postinstall skipifsilent
