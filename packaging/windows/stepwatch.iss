; Installateur Windows de StepWatch (Inno Setup 6).
; Construit par la CI : iscc /DAppVersion=X.Y.Z /DSourceDir=dist\StepWatch /DIconFile=... /DOutputDir=...
; Installation dans le profil de l'utilisateur, sans droits administrateur :
; StepWatch peut ensuite se mettre à jour lui-même.

#ifndef AppVersion
  #error "Indiquez /DAppVersion=X.Y.Z"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\StepWatch"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif
#ifndef IconFile
  #define IconFile "..\..\build\windows\stepwatch.ico"
#endif

[Setup]
AppId={{6F0C9E52-6E4B-4B8E-9C47-2D7A5E0B3F11}
AppName=StepWatch
AppVersion={#AppVersion}
AppVerName=StepWatch {#AppVersion}
AppPublisher=Projet StepWatch
AppPublisherURL=https://github.com/1002-AZ-2001/StepWatch
AppSupportURL=https://github.com/1002-AZ-2001/StepWatch/issues
AppUpdatesURL=https://github.com/1002-AZ-2001/StepWatch/releases
VersionInfoVersion={#AppVersion}.0
VersionInfoDescription=Installateur de StepWatch
VersionInfoCompany=Projet StepWatch
VersionInfoCopyright=Projet StepWatch — licence GNU AGPL v3
DefaultDirName={localappdata}\Programs\StepWatch
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=StepWatch-{#AppVersion}-windows-x64-setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\StepWatch.exe
UninstallDisplayName=StepWatch
LicenseFile=..\..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Une seule instance de StepWatch : même mutex que l'application.
AppMutex=Local\StepWatch-instance-unique
CloseApplications=no

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Fichiers d'une version précédente qui ne font plus partie de l'application.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\StepWatch"; Filename: "{app}\StepWatch.exe"; Comment: "Documentation des interventions"
Name: "{autodesktop}\StepWatch"; Filename: "{app}\StepWatch.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\StepWatch.exe"; Description: "{cm:LaunchProgram,StepWatch}"; Flags: nowait postinstall skipifsilent
