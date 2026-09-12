; Inno Setup script for AutoPrint.
; Version is read directly from the PyInstaller EXE so there is one source of truth.
#define MyAppName "AutoPrint"
#define MyAppPublisher "AutoPrint"
#define MyAppExeName "AutoPrint.exe"
#define MyAppExePath "..\dist\AutoPrint\AutoPrint.exe"
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
OutputBaseFilename=AutoPrint_Setup_{#MyAppVersion}
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
Source: "..\dist\AutoPrint\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\AutoPrint"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\AutoPrint"; Filename: "{app}\{#MyAppExeName}"

[Run]
; Normal/manual install: offer to launch from the final wizard page.
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить {#MyAppName}"; Flags: nowait postinstall skipifsilent
; Updater uses /VERYSILENT: restart automatically after the files are replaced.
Filename: "{app}\{#MyAppExeName}"; Flags: nowait skipifnotsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    { Keep an existing install directory so user config, licenses, templates, and data }
    { remain in place, then remove only obsolete application-owned files. }
    DeleteFile(ExpandConstant('{app}\BarTenderGUI.exe'));
    DeleteFile(ExpandConstant('{group}\BarTender GUI.lnk'));
    DeleteFile(ExpandConstant('{autodesktop}\BarTender GUI.lnk'));
  end;
end;
