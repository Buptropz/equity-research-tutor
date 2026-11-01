Option Explicit
Dim shell, fso, base, pythonw, candidate, process, line
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = shell.ExpandEnvironmentStrings("%USERPROFILE%") & "\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"
If Not fso.FileExists(pythonw) Then
  pythonw = ""
  Set process = shell.Exec("where.exe pythonw.exe")
  Do While Not process.StdOut.AtEndOfStream
    line = Trim(process.StdOut.ReadLine)
    If fso.FileExists(line) And pythonw = "" Then pythonw = line
  Loop
End If
If pythonw = "" Then
  MsgBox "Python was not found. Please install Python 3.11 or later, then try again.", 48, "Research Notebook"
Else
  shell.Run Chr(34) & pythonw & Chr(34) & " " & Chr(34) & base & "\launcher.py" & Chr(34), 0, False
End If
