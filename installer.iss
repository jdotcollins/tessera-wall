; Inno Setup script for Tessera Wall. Built by build.ps1 (which passes /DAppVersion=...).
; Installs for the current user only: no administrator prompt, nothing outside the user's profile.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{0799EF0A-5304-4EEB-849D-02DAB498FB5D}
AppName=Tessera Wall
AppVersion={#AppVersion}
AppPublisher=Tessera Wall contributors
DefaultDirName={autopf}\Tessera Wall
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
DisableDirPage=yes
OutputDir=build\installer
OutputBaseFilename=TesseraWall-setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=tesserawall.ico
UninstallDisplayIcon={app}\TesseraWall.exe
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
; tell Explorer the right-click entries changed, so they appear without signing out
ChangesAssociations=yes

[Tasks]
Name: "desktopicon"; Description: "Put a shortcut on the desktop"
Name: "contextmenu"; Description: "Add ""Play together in Tessera Wall"" to the right-click menu for videos and folders"

[Files]
Source: "build\dist\TesseraWall\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Tessera Wall"; Filename: "{app}\TesseraWall.exe"
Name: "{autodesktop}\Tessera Wall"; Filename: "{app}\TesseraWall.exe"; Tasks: desktopicon
Name: "{usersendto}\Tessera Wall"; Filename: "{app}\TesseraWall.exe"
Name: "{usersendto}\Tessera Wall (random 6)"; Filename: "{app}\TesseraWall.exe"; Parameters: "--random 6"

[Registry]
; Right-click entry for video files. Explorer still starts one Tessera Wall per selected file; the first
; becomes the wall and the rest hand their file to it (claim_control_pipe in tesserawall.pyw).
; MultiSelectModel=Player only keeps the entry visible when more than 15 files are selected.
; Parent keys first: uninstall runs in reverse order, so these go after the verb keys, and only if empty.
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\video"; Flags: uninsdeletekeyifempty; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\video\shell"; Flags: uninsdeletekeyifempty; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\video\shell\tesserawall"; ValueType: string; ValueName: ""; ValueData: "Play together in Tessera Wall"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\video\shell\tesserawall"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Player"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\video\shell\tesserawall"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\TesseraWall.exe"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\video\shell\tesserawall\command"; ValueType: string; ValueName: ""; ValueData: """{app}\TesseraWall.exe"" ""%1"""; Tasks: contextmenu
; The same entry per extension. Needed because Windows ignores the "video" entry above for any
; extension that has its own SystemFileAssociations\.ext key with a context-menu handler, which
; .mp4, .mkv, .mov etc. do (the built-in "Cast to device"). Only the extension's own shell\tesserawall
; key is removed on uninstall, never the .ext key itself.
#dim Ext[11] {"mp4", "mkv", "webm", "mov", "avi", "m4v", "wmv", "flv", "m2ts", "mpg", "mpeg"}
#define i
#sub ExtEntry
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.{#Ext[i]}"; Flags: uninsdeletekeyifempty; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.{#Ext[i]}\shell"; Flags: uninsdeletekeyifempty; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.{#Ext[i]}\shell\tesserawall"; ValueType: string; ValueName: ""; ValueData: "Play together in Tessera Wall"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.{#Ext[i]}\shell\tesserawall"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Player"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.{#Ext[i]}\shell\tesserawall"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\TesseraWall.exe"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.{#Ext[i]}\shell\tesserawall\command"; ValueType: string; ValueName: ""; ValueData: """{app}\TesseraWall.exe"" ""%1"""; Tasks: contextmenu
#endsub
#for {i = 0; i < DimOf(Ext); i++} ExtEntry
; Right-click entry for folders.
Root: HKCU; Subkey: "Software\Classes\Directory\shell\tesserawall"; ValueType: string; ValueName: ""; ValueData: "Play together in Tessera Wall"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Directory\shell\tesserawall"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\TesseraWall.exe"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Directory\shell\tesserawall\command"; ValueType: string; ValueName: ""; ValueData: """{app}\TesseraWall.exe"" ""%1"""; Tasks: contextmenu
; ... and for choosing a template for that folder.
Root: HKCU; Subkey: "Software\Classes\Directory\shell\tesserawall-template"; ValueType: string; ValueName: ""; ValueData: "Play as a template in Tessera Wall"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Directory\shell\tesserawall-template"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\TesseraWall.exe"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Directory\shell\tesserawall-template\command"; ValueType: string; ValueName: ""; ValueData: """{app}\TesseraWall.exe"" --choose-template ""%1"""; Tasks: contextmenu

[Run]
Filename: "{app}\TesseraWall.exe"; Description: "Start Tessera Wall now"; Flags: nowait postinstall skipifsilent
