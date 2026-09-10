; Inno Setup script for AutoPrint / BarTenderGUI.
; Version is read directly from the PyInstaller EXE so there is one source of truth.
#define MyAppName "BarTenderGUI"
#define MyAppPublisher "AutoPrint"
#define MyAppExeName "BarTenderGUI.exe"
#define MyAppExePath "..\dist\BarTenderGUI\BarTenderGUI.exe"
#define MyAppVersion GetFileVersionString(MyAppExePath)
#define MyAppVersionNumbers GetVersionNumbersString(MyAppExePath)

[Setup]
AppId={{B9F5C8A4-4F63-4B43-B93B-FA2C1E25B111}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} version {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=BarTenderGUI_Setup_{#MyAppVersion}
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
DirExistsWarning=no
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#MyAppVersionNumbers}
VersionInfoProductVersion={#MyAppVersionNumbers}
VersionInfoProductTextVersion={#MyAppVersion}

[Files]
; Application binaries are replaced on every update.
Source: "..\dist\BarTenderGUI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Operational/user data must survive updates and uninstall. These files are optional in CI.
Source: "..\product_catalog.xlsx"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist skipifsourcedoesntexist uninsneveruninstall
Source: "..\tmp_batch.csv"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist skipifsourcedoesntexist uninsneveruninstall
Source: "..\config\stats.json"; DestDir: "{app}\config"; Flags: ignoreversion onlyifdoesntexist skipifsourcedoesntexist uninsneveruninstall
Source: "..\config\config.json"; DestDir: "{app}\config"; Flags: ignoreversion onlyifdoesntexist skipifsourcedoesntexist uninsneveruninstall
Source: "..\templates\*.btw"; DestDir: "{app}\templates"; Flags: ignoreversion onlyifdoesntexist skipifsourcedoesntexist recursesubdirs createallsubdirs uninsneveruninstall

[Icons]
Name: "{group}\BarTender GUI"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\BarTender GUI"; Filename: "{app}\{#MyAppExeName}"

[Run]
; Normal/manual install: offer to launch from the final wizard page.
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить {#MyAppName}"; Flags: nowait postinstall skipifsilent
; Updater uses /VERYSILENT: restart automatically after the files are replaced.
Filename: "{app}\{#MyAppExeName}"; Flags: nowait skipifnotsilent
