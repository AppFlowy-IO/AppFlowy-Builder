; Real Inno Setup installation, scoped entirely to a controller-owned directory.
[Setup]
AppId=AppFlowyUpdateE2E-{#CaseId}
AppName=AppFlowy Update E2E
AppVersion={#Version}
DefaultDirName={#InstallDir}
UsePreviousAppDir=no
PrivilegesRequired=lowest
Uninstallable=no
CreateUninstallRegKey=no
DisableDirPage=yes
DisableProgramGroupPage=yes
CloseApplications=no
RestartApplications=no
OutputDir={#OutputDir}
OutputBaseFilename=update
Compression=lzma2
[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion
[Run]
Filename: "{app}\AppFlowy.exe"; Flags: nowait
