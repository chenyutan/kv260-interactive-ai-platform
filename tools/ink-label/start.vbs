Option Explicit
Dim fs, shell, folder, runtimeFile, runtime, executable
Set fs = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
folder = fs.GetParentFolderName(WScript.ScriptFullName)
runtimeFile = folder & "\runtime.txt"
If Not fs.FileExists(runtimeFile) Then
    MsgBox "Please run install.cmd first.", 48, "InkLabel"
    WScript.Quit
End If
runtime = Trim(fs.OpenTextFile(runtimeFile, 1).ReadAll)
executable = fs.BuildPath(fs.GetParentFolderName(runtime), "pythonw.exe")
If Not fs.FileExists(executable) Then
    MsgBox "Python runtime missing. Please run install.cmd.", 48, "InkLabel"
    WScript.Quit
End If
shell.CurrentDirectory = folder
shell.Run Chr(34) & executable & Chr(34) & " " & Chr(34) & folder & "\startup.py" & Chr(34), 0, False
