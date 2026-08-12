Set ws = CreateObject("WScript.Shell")
Set sc = ws.CreateShortcut("C:\Users\zhaoting1\Desktop\GEO生文审核系统.lnk")
sc.TargetPath = "C:\Users\zhaoting1\Desktop\GEO生文审核 - 副本\启动审核系统.bat"
sc.WorkingDirectory = "C:\Users\zhaoting1\Desktop\GEO生文审核 - 副本"
sc.Description = "GEO 生文审核系统启动器"
sc.IconLocation = "C:\Windows\System32\shell32.dll,13"
sc.Save
