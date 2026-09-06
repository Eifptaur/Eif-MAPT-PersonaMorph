' wx-agent stopper (no window): stops bot + watchdog by PID files (no PowerShell).
Option Explicit
Dim fso, sh, root, script, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
script = root & "\scripts\stop_bot.py"
On Error Resume Next
cmd = "pyw.exe -3 """ & script & """"
sh.Run cmd, 0, True
If Err.Number <> 0 Then
    Err.Clear
    cmd = "pythonw.exe """ & script & """"
    sh.Run cmd, 0, True
    If Err.Number <> 0 Then
        Err.Clear
        cmd = "python.exe """ & script & """"
        sh.Run cmd, 0, True
    End If
End If
MsgBox "wx-agent stopped (bot + watchdog).", 64, "wx-agent"
