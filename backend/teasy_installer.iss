; Inno Setup script for TEasy.
; Build with Inno Setup (https://jrsoftware.org/isinfo.php), AFTER running
; `pyinstaller teasy.spec` from inside backend/ so dist\TEasy\TEasy.exe exists.
;
; Open this file in the Inno Setup Compiler and click Build, or from the
; command line:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" teasy_installer.iss
;
; Output: backend\installer_output\TEasy-Setup.exe

#define MyAppName "TEasy"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "TEasy"
#define MyAppExeName "TEasy.exe"

[Setup]
AppId={{6B8B6C1E-7E7B-4A5A-9C6E-2B6A6C0E1A11}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=TEasy-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; Change to no if you want per-machine (needs admin) instead of per-user installs
PrivilegesRequired=lowest

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
; Pulls in the whole PyInstaller output folder (exe + all its support files)
Source: "dist\TEasy\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent
