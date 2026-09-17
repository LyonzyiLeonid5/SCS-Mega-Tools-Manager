[Setup]
AppId={{D47C4FCD-D4C1-4BA4-845B-E5417AB2CA29}}
AppName=SCS Tools Manager
AppVersion=1.0.0
AppPublisher=SCS Tools Manager
DefaultDirName={autopf}\SCS Tools Manager
DefaultGroupName=SCS Tools Manager
DisableProgramGroupPage=yes
UninstallDisplayName=SCS Tools Manager
OutputDir=D:\Games\Euro Truck Simulator 2\Tools\SCS Mega Manager\dist\Windows
OutputBaseFilename=SCS_Tools_Manager_Setup
SetupIconFile=D:\Games\Euro Truck Simulator 2\Tools\SCS Mega Manager\assets\icon.ico
LicenseFile=D:\Games\Euro Truck Simulator 2\Tools\SCS Mega Manager\LICENSE
UninstallDisplayIcon={app}\SCS_Tools_Manager.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "D:\Games\Euro Truck Simulator 2\Tools\SCS Mega Manager\build\release\SCS_Tools_Manager\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "D:\Games\Euro Truck Simulator 2\Tools\SCS Mega Manager\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "startmenu"; Description: "Create an SCS Tools Manager folder in the Start menu"; Flags: unchecked

[Icons]
Name: "{autodesktop}\SCS Tools Manager"; Filename: "{app}\SCS_Tools_Manager.exe"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{autoprograms}\SCS Tools Manager\SCS Tools Manager"; Filename: "{app}\SCS_Tools_Manager.exe"; WorkingDir: "{app}"; Tasks: startmenu

[Registry]
Root: HKCR; Subkey: ".scstool"; ValueType: string; ValueName: ""; ValueData: "SCS.Tools.Manager.scstool"; Flags: uninsdeletevalue
Root: HKCR; Subkey: "SCS.Tools.Manager.scstool"; ValueType: string; ValueName: ""; ValueData: "SCS Tools Manager tool"; Flags: uninsdeletekey
Root: HKCR; Subkey: "SCS.Tools.Manager.scstool\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\SCS_Tools_Manager.exe,0"
Root: HKCR; Subkey: "SCS.Tools.Manager.scstool\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\SCS_Tools_Manager.exe"" ""%1"""

