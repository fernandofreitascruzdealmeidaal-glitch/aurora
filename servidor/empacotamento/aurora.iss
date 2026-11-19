; Instalador da Aurora para Windows (Inno Setup 6).
; Gerado automaticamente pelo GitHub Actions (.github/workflows/windows.yml).

#define Versao GetEnv("AURORA_VERSAO")
#if Versao == ""
  #define Versao "0.2.0"
#endif

[Setup]
AppId={{B6E0F2A4-6B1C-4C0E-9E59-3A2F9A1D7C11}
AppName=Aurora
AppVersion={#Versao}
AppVerName=Aurora {#Versao}
AppPublisher=Projeto escolar Aurora
AppPublisherURL=https://github.com/fernandofreitascruzdealmeidaal-glitch/aurora
; Instala só para o usuário atual: não pede senha de administrador.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\Aurora
DefaultGroupName=Aurora
DisableProgramGroupPage=yes
DisableDirPage=yes
OutputDir=..\dist
OutputBaseFilename=Aurora-Instalador-Windows
SetupIconFile=aurora.ico
UninstallDisplayIcon={app}\Aurora.exe
UninstallDisplayName=Aurora
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Fecha a Aurora se estiver aberta durante uma atualização.
CloseApplications=yes

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "atalho"; Description: "Criar um atalho na Área de Trabalho"; GroupDescription: "Atalhos:"

[Files]
Source: "..\dist\Aurora\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Aurora"; Filename: "{app}\Aurora.exe"; Comment: "Sustentabilidade em Minas Gerais"
Name: "{autodesktop}\Aurora"; Filename: "{app}\Aurora.exe"; Tasks: atalho; Comment: "Sustentabilidade em Minas Gerais"

[Run]
Filename: "{app}\Aurora.exe"; Description: "Abrir a Aurora agora"; Flags: nowait postinstall skipifsilent

; Os dados (notícias aprovadas, senha da equipe, leituras dos sensores) ficam em
; %LOCALAPPDATA%\Aurora e NÃO são apagados ao desinstalar.
