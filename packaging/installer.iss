#define AppVersion "1.0.0rc1"
[Setup]
AppId={{8DAD4594-FBC3-43A7-AACB-3849CAB7C772}
AppName=CASSound
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\CASSound
DefaultGroupName=CASSound
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=CASSound-Setup-{#AppVersion}-x64
SetupIconFile=..\src\cassound\resources\cassound.ico
LicenseFile=..\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
[Files]
Source: "..\dist\CASSound\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\CASSound"; Filename: "{app}\CASSound.exe"
Name: "{autodesktop}\CASSound"; Filename: "{app}\CASSound.exe"; Tasks: desktopicon
[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked
[Run]
Filename: "{app}\CASSound.exe"; Description: "Open CASSound"; Flags: nowait postinstall skipifsilent
