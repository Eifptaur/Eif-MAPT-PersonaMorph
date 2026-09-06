' wx-agent launcher (no window): starts the hidden watchdog (pyw/pythonw hidden).
' The project does NOT need PowerShell at all - no cmd/PowerShell window is shown.
Option Explicit
Dim fso, sh, root, script, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
script = root & "\scripts\watchdog.py"
On Error Resume Next
cmd = "pyw.exe -3 """ & script & """"
sh.Run cmd, 0, False
If Err.Number <> 0 Then
    Err.Clear
    cmd = "pythonw.exe """ & script & """"
    sh.Run cmd, 0, False
    If Err.Number <> 0 Then
        Err.Clear
        cmd = "python.exe """ & script & """"
        sh.Run cmd, 0, False
    End If
End If
