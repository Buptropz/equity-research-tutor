param([ValidateRange(1,4)][int]$Design = 1)
$ErrorActionPreference = 'Stop'
$designFiles = @('01-library-red.ico', '02-seal-red.ico', '03-insight-red.ico', '04-value-red.ico')
$iconPath = Join-Path $PSScriptRoot ('assets/icons/' + $designFiles[$Design - 1])
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) '研习股票研究.lnk'
if (-not (Test-Path -LiteralPath $iconPath)) { throw '找不到图标文件，请将完整程序文件放在一起。' }
if (-not (Test-Path -LiteralPath $shortcutPath)) { throw '找不到桌面的研习股票研究快捷方式。' }
$backupFolder = Join-Path $PSScriptRoot 'data/shortcut-backups'
New-Item -ItemType Directory -Path $backupFolder -Force | Out-Null
Copy-Item -LiteralPath $shortcutPath -Destination (Join-Path $backupFolder ('研习股票研究-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.lnk'))
$shortcutTool = New-Object -ComObject WScript.Shell
$researchShortcut = $shortcutTool.CreateShortcut($shortcutPath)
$researchShortcut.IconLocation = $iconPath + ',0'
$researchShortcut.Save()
if (-not ('YanxiIconRefresh' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class YanxiIconRefresh {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    public static extern void SHChangeNotify(uint eventId, uint flags, string item1, IntPtr item2);
}
'@
}
[YanxiIconRefresh]::SHChangeNotify(0x00002000, 0x0005, $shortcutPath, [IntPtr]::Zero)
[YanxiIconRefresh]::SHChangeNotify(0x08000000, 0, $null, [IntPtr]::Zero)
Write-Output ('桌面图标已更新，方案 ' + $Design + '；原启动设置已保留。')
