; Inno Setup script for Contract2BPMN (built by GitHub Actions on windows-latest).
; Input: app\dist\Contract2BPMN\ (PyInstaller one-folder build). Output: app\dist\Contract2BPMN-Setup-x64.exe
#define AppName "Contract2BPMN"
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6F0C7E2A-3B9D-4C55-9E7A-2D1C5B8A4F10}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Contract2BPMN authors
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=..\..\dist
OutputBaseFilename=Contract2BPMN-Setup-x64
Compression=lzma2/fast
SolidCompression=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern
DisableProgramGroupPage=yes

[Files]
Source: "..\..\dist\Contract2BPMN\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\Contract2BPMN.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\Contract2BPMN.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Run]
Filename: "{app}\Contract2BPMN.exe"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent
