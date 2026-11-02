; Build after python build_windows.py with Inno Setup 6.7+ or 7:
; ISCC.exe /DAppVersion=1.0.1 packaging/windows-installer.iss
#ifndef AppVersion
  #error Specify /DAppVersion to match the packaged application.
#endif

[Setup]
AppId={{4F04BA53-56F7-4924-AE38-08CE7292B862}
AppName=Agent 工坊
AppVersion={#AppVersion}
AppPublisher=Agent Workbench
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\AgentWorkbench
DefaultGroupName=Agent 工坊
DisableProgramGroupPage=yes
DisableDirPage=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..
OutputBaseFilename=AgentWorkbench-{#AppVersion}-Setup
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\AgentWorkbench.exe
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
CloseApplications=no
RestartApplications=no
AppMutex=Local\AgentWorkbench.Service,Local\AgentWorkbench.Startup

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\AgentWorkbench\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Agent 工坊"; Filename: "{app}\AgentWorkbench.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Agent 工坊"; Filename: "{app}\AgentWorkbench.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\AgentWorkbench.exe"; Description: "{cm:LaunchProgram,Agent 工坊}"; Flags: nowait postinstall skipifsilent
