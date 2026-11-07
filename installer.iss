; Inno Setup script for dist\SchemaEngine-Setup-<version>.exe. build.ps1 passes /DVersion=...
; Installs for the current user only, so no admin prompt. Uninstalling leaves %LOCALAPPDATA%\SchemaEngine
; and the settings in ~\.config\schema-engine; saved keys stay in Credential Manager.

#ifndef Version
  #define Version "0.0.0"
#endif

[Setup]
AppId={{AA3704E9-7B65-4A66-9901-1B4D8799C9AB}
AppName=Schema Engine
AppVersion={#Version}
AppVerName=Schema Engine {#Version}
AppPublisher=Georgy Esik
DefaultDirName={localappdata}\Programs\Schema Engine
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=SchemaEngine-Setup-{#Version}
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\SchemaEngine.exe
UninstallDisplayName=Schema Engine
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\SchemaEngine\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; files from an older version that this one no longer ships
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\Schema Engine"; Filename: "{app}\SchemaEngine.exe"
Name: "{autodesktop}\Schema Engine"; Filename: "{app}\SchemaEngine.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\SchemaEngine.exe"; Description: "{cm:LaunchProgram,Schema Engine}"; Flags: nowait postinstall skipifsilent
