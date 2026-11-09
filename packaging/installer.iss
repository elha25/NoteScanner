; Inno Setup-skript for NoteScanner. Installeres per bruker (krever ikke administrator), slik at appen kan
; oppdatere seg selv. Bygg:  iscc /DMyAppVersion=2.3.0 packaging\installer.iss
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

[Setup]
AppId={{6E3B7F2A-4C1D-4E8B-9A57-2D0C1F4B8E93}
AppName=NoteScanner
AppVersion={#MyAppVersion}
AppVerName=NoteScanner {#MyAppVersion}
AppPublisher=Elias Haaland
AppPublisherURL=https://github.com/elha25/NoteScanner
AppSupportURL=https://github.com/elha25/NoteScanner/issues
AppUpdatesURL=https://github.com/elha25/NoteScanner/releases
DefaultDirName={localappdata}\Programs\NoteScanner
DefaultGroupName=NoteScanner
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=NoteScanner-Setup-{#MyAppVersion}
SetupIconFile=NoteScanner.ico
UninstallDisplayIcon={app}\NoteScanner.exe
UninstallDisplayName=NoteScanner
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
AppMutex=NoteScannerAppMutex
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "nb"; MessagesFile: "compiler:Languages\Norwegian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; fjern filer fra forrige versjon, så gamle biblioteker ikke blir liggende igjen
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\NoteScanner\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\NoteScanner"; Filename: "{app}\NoteScanner.exe"
Name: "{autodesktop}\NoteScanner"; Filename: "{app}\NoteScanner.exe"; Tasks: desktopicon

[UninstallRun]
; fjern koblingen til skannerknappen (virker bare hvis den ble lagt inn; krever ellers ingenting)
Filename: "{app}\NoteScanner.exe"; Parameters: "--fjern-knapp --stille"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "FjernKnapp"

[Run]
Filename: "{app}\NoteScanner.exe"; Description: "{cm:LaunchProgram,NoteScanner}"; Flags: nowait postinstall skipifsilent
; ved oppdatering fra appen (stille installasjon) startes appen igjen automatisk
Filename: "{app}\NoteScanner.exe"; Flags: nowait; Check: WizardSilent
