$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'The packaged Windows runtime smoke requires Windows.' }
Add-Type -AssemblyName System.Windows.Forms
$session = (Get-Process -Id $PID).SessionId
$screen = [System.Windows.Forms.SystemInformation]::VirtualScreen
$capabilities = [ordered]@{
    ImageOS = $env:ImageOS
    ImageVersion = $env:ImageVersion
    OS = [Environment]::OSVersion.VersionString
    Architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
    PowerShell = $PSVersionTable.PSVersion.ToString()
    UserInteractive = [Environment]::UserInteractive
    SessionId = $session
    ScreenWidth = $screen.Width
    ScreenHeight = $screen.Height
    Taskkill = (Get-Command taskkill.exe -ErrorAction Stop).Source
    ProcessQuery = @(Get-CimInstance Win32_Process -Filter "ProcessId = $PID").Count -eq 1
}
$capabilities | ConvertTo-Json
if (-not $capabilities.UserInteractive -or $session -eq 0 -or
    $screen.Width -le 0 -or $screen.Height -le 0 -or -not $capabilities.ProcessQuery) {
    throw 'Runner lacks an interactive desktop or process-query capability; runtime smoke cannot run.'
}
