; Inno Setup 6 script: MarbleTrack installer (per-machine, Start menu + optional desktop shortcut).
#define AppVersion "1.0.0"
[Setup]
AppId={{6C1C2B0E-3E52-4D7A-9A55-9C0D3B7E2F11}
AppName=MarbleTrack
AppVersion={#AppVersion}
AppPublisher=MarbleTrack
DefaultDirName={autopf}\MarbleTrack
DefaultGroupName=MarbleTrack
OutputDir=..\dist
OutputBaseFilename=MarbleTrack-Setup-{#AppVersion}
SetupIconFile=marbletrack.ico
UninstallDisplayIcon={app}\MarbleTrack.exe
Compression=lzma2/max
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
PrivilegesRequiredOverridesAllowed=dialog

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\MarbleTrack\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\MarbleTrack"; Filename: "{app}\MarbleTrack.exe"
Name: "{group}\MarbleTrack (demo cameras)"; Filename: "{app}\MarbleTrack.exe"; Parameters: "--demo"
Name: "{group}\MarbleTrack logs"; Filename: "{userappdata}\MarbleTrack\logs"
Name: "{autodesktop}\MarbleTrack"; Filename: "{app}\MarbleTrack.exe"; Tasks: desktopicon

[Run]
; NDI cameras are found by multicast DNS and stream over the network: let MarbleTrack receive them
; without the firewall question (a new camera network is often classed as Public). Its own UI
; server listens on 127.0.0.1 only. Needs an all-users install; otherwise Windows asks on first start.
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""MarbleTrack (NDI cameras)"""; Flags: runhidden; Check: IsAdminInstallMode
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""MarbleTrack (NDI cameras)"" dir=in action=allow program=""{app}\MarbleTrack.exe"" enable=yes profile=any"; Flags: runhidden; Check: IsAdminInstallMode; StatusMsg: "Allowing MarbleTrack through the Windows firewall..."
Filename: "{app}\MarbleTrack.exe"; Description: "Start MarbleTrack"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""MarbleTrack (NDI cameras)"""; Flags: runhidden; Check: IsAdminInstallMode; RunOnceId: "MarbleTrackFirewall"

[Code]
function WebView2Installed(): Boolean;
var v: String;
begin
  Result := RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) or
            RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v);
  Result := Result and (v <> '') and (v <> '0.0.0.0');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var ErrorCode: Integer;
begin
  if (CurStep = ssPostInstall) and not WebView2Installed() then
    if MsgBox('MarbleTrack shows its window with the Microsoft Edge WebView2 Runtime, which is not installed on this PC ' +
              '(Windows 11 includes it). Without it MarbleTrack opens in your web browser instead.' + #13#10#13#10 +
              'Open the download page now?', mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', 'https://developer.microsoft.com/microsoft-edge/webview2/', '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;
