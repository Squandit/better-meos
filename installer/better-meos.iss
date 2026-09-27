; better-meos Windows installer (Inno Setup 6).
;
; Built by .github/workflows/build-exe.yml after PyInstaller has made
; dist\better-meos.exe:   ISCC /DAppVersion=1.2.0 installer\better-meos.iss
;
; Installs for the current user (no admin prompt, fine on locked-down club
; laptops) into %LOCALAPPDATA%\Programs\better-meos, with Start menu and
; desktop shortcuts. The app keeps its data in %USERPROFILE%\better-meos
; (see launcher.py), so installing, upgrading and uninstalling never touch
; events, settings or the runner database.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F0B8C3E-2B7D-4C8E-9A51-B3E2A0C7D941}
AppName=better-meos
AppVersion={#AppVersion}
AppVerName=better-meos {#AppVersion}
AppPublisher=better-meos
DefaultDirName={autopf}\better-meos
DefaultGroupName=better-meos
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=better-meos-setup
SetupIconFile=better-meos.ico
UninstallDisplayIcon={app}\better-meos.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Close a running better-meos before replacing it on upgrade.
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Put a better-meos icon on the desktop"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\better-meos.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\better-meos"; Filename: "{app}\better-meos.exe"; WorkingDir: "{%USERPROFILE}\better-meos"
Name: "{group}\Events and settings folder"; Filename: "{%USERPROFILE}\better-meos"
Name: "{group}\Uninstall better-meos"; Filename: "{uninstallexe}"
Name: "{autodesktop}\better-meos"; Filename: "{app}\better-meos.exe"; WorkingDir: "{%USERPROFILE}\better-meos"; Tasks: desktopicon

[Dirs]
; Created up front so the Start menu's folder shortcut works before first run.
Name: "{%USERPROFILE}\better-meos"; Flags: uninsneveruninstall
Name: "{%USERPROFILE}\better-meos\events"; Flags: uninsneveruninstall

[Run]
Filename: "{app}\better-meos.exe"; Description: "Start better-meos now"; Flags: nowait postinstall skipifsilent

[Messages]
FinishedLabel=better-meos is installed.%n%nYour events and settings are kept in the better-meos folder in your user folder (C:\Users\<you>\better-meos), and stay there if you ever uninstall.%n%nWindows may ask whether better-meos can use the network the first time it starts: allow it on private networks so phones on the same WiFi can see results and enter.
