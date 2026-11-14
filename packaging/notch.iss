; Inno Setup script for Notch (Windows installer).
; Build with:  ISCC.exe /DMyAppVersion=1.6.0 packaging\notch.iss
; Expects a frozen build in ..\dist\Notch (produced by packaging\notch.spec).

#ifndef MyAppVersion
  #define MyAppVersion "1.6.0"
#endif

#define MyAppName "Notch"
#define MyAppPublisher "Socia"
#define MyAppExeName "Notch.exe"

[Setup]
AppId={{A3F1C9D2-6B84-4E17-9C55-2D8F4A71B0E3}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Notch
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Per-user install: no UAC prompt, and {app} stays writable for notch_log.txt
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=NotchSetup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=notch.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\Notch\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
