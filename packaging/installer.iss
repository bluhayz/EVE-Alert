; EVE Alert Windows installer (#179, v8.0)
;
; Wraps the PyInstaller-built EVE-Alert.exe with a Start Menu shortcut,
; an optional (off-by-default) auto-start-with-Windows entry, and a
; clean uninstall that preserves user config/data under
; %APPDATA%\evealert (settings, pilot history, statistics, plugins)
; unless the user explicitly opts to delete it.
;
; Build (from the repo root, after PyInstaller has produced
; dist\EVE-Alert.exe):
;   iscc packaging\installer.iss /DMyAppVersion=8.0.0
;
; Requires Inno Setup 6 (https://jrsoftware.org/isinfo.php) -- iscc.exe
; on PATH, or the release workflow's own path detection (see release.yml).

#define MyAppName "EVE Alert"
#define MyAppPublisher "bluhayz"
#define MyAppURL "https://github.com/bluhayz/EVE-Alert"
#define MyAppExeName "EVE-Alert.exe"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif

[Setup]
; Fixed GUID -- must never change across releases, it's how Windows
; recognizes "this is an upgrade of the same app" rather than a
; side-by-side install.
AppId={{B4A1E4A0-6B5A-4B9C-9C1E-EVEALERT80000}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=EVE-Alert-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
; Per-user, no admin elevation required -- EVE Alert already reads/
; writes all of its own state under %APPDATA%\evealert regardless of
; where the .exe itself is installed.
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "autostart"; Description: "Start EVE Alert automatically when Windows starts"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "..\dist\EVE-Alert.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Off by default -- the "autostart" task above starts unchecked.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExeName}"""; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeUninstall(): Boolean;
begin
  Result := True;
  if MsgBox('Also delete your EVE Alert settings and data (pilot history, statistics, plugins)?' + #13#10 +
             'This cannot be undone.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
  begin
    DelTree(ExpandConstant('{userappdata}\evealert'), True, True, True);
  end;
end;
