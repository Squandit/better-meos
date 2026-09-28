; Control Windows installer (Inno Setup 6).
;
; Built by .github/workflows/build-exe.yml after PyInstaller has made
; dist\control-orienteering.exe:
;     ISCC /DAppVersion=1.3.0 installer\control-orienteering.iss
;
; Installs for the current user (no admin prompt, fine on locked-down club
; laptops) into %LOCALAPPDATA%\Programs\control-orienteering, with Start menu
; and desktop shortcuts. The app keeps its data in
; %USERPROFILE%\control-orienteering (see launcher.py), so installing,
; upgrading and uninstalling never touch events, settings or the runner
; database.
;
; Until 1.3.0 the app was called better-meos. The AppId is unchanged, so this
; upgrades an old install in place of adding a second one; [InstallDelete]
; clears the old program folder and shortcuts, and the app moves the old data
; folder across itself on first start.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F0B8C3E-2B7D-4C8E-9A51-B3E2A0C7D941}
AppName=Control
AppVersion={#AppVersion}
AppVerName=Control {#AppVersion}
AppPublisher=Control
DefaultDirName={autopf}\control-orienteering
DefaultGroupName=Control
; Don't reuse the old better-meos folder and Start menu group on upgrade.
UsePreviousAppDir=no
UsePreviousGroup=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=control-orienteering-setup
SetupIconFile=control-orienteering.ico
UninstallDisplayIcon={app}\control-orienteering.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Close a running copy before replacing it on upgrade.
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Put a Control icon on the desktop"; GroupDescription: "Shortcuts:"

[InstallDelete]
; What an install from before the rename left behind (program files only).
Type: filesandordirs; Name: "{autopf}\better-meos"
Type: filesandordirs; Name: "{autoprograms}\better-meos"
Type: files; Name: "{autodesktop}\better-meos.lnk"

[Files]
Source: "..\dist\control-orienteering.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Control"; Filename: "{app}\control-orienteering.exe"; WorkingDir: "{%USERPROFILE}\control-orienteering"
Name: "{group}\Events and settings folder"; Filename: "{%USERPROFILE}\control-orienteering"
Name: "{group}\Uninstall Control"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Control"; Filename: "{app}\control-orienteering.exe"; WorkingDir: "{%USERPROFILE}\control-orienteering"; Tasks: desktopicon

[Dirs]
; Created up front so the Start menu's folder shortcut works before first run
; (an empty one doesn't stop the app moving an old better-meos folder in).
Name: "{%USERPROFILE}\control-orienteering"; Flags: uninsneveruninstall
Name: "{%USERPROFILE}\control-orienteering\events"; Flags: uninsneveruninstall

[Run]
Filename: "{app}\control-orienteering.exe"; Description: "Start Control now"; Flags: nowait postinstall skipifsilent

[Messages]
FinishedLabel=Control is installed.%n%nYour events and settings are kept in the control-orienteering folder in your user folder (C:\Users\<you>\control-orienteering), and stay there if you ever uninstall. If you used better-meos before, its folder moves there the first time Control starts.%n%nWindows may ask whether Control can use the network the first time it starts: allow it on private networks so phones on the same WiFi can see results and enter.
