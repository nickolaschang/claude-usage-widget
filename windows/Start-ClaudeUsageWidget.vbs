' Launches the Claude usage widget with no console window.
' Tries PowerShell 7 (pwsh) first and falls back to Windows PowerShell.
Option Explicit
Dim shell, fso, here, args

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
args = " -NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & here & "\ClaudeUsageWidget.ps1"""

On Error Resume Next
shell.Run "pwsh.exe" & args, 0, False
If Err.Number <> 0 Then
    Err.Clear
    shell.Run "powershell.exe" & args, 0, False
End If
