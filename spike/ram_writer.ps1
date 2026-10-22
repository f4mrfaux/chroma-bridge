# SPIKE: run elevated. Polls ram_frame.bin (5 RGB triplets written by fakebox.py from Chroma
# channel 0) and pushes changes to the Viper Steel RGB controller at SMBus 0x77 via PawnIO.
# Only the proven Steel writes are ever sent to 0x77: write(0x77, 0x17+led, R) then write(0x77, G, B).
param([int]$Hz = 20)
$frame = Join-Path $PSScriptRoot 'ram_frame.bin'
$dir = 'C:\Tools\OpenRGB\OpenRGB Windows 64-bit'
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class P {
  [DllImport(@"$dir\PawnIOLib.dll")] public static extern int pawnio_open(out IntPtr h);
  [DllImport(@"$dir\PawnIOLib.dll")] public static extern int pawnio_load(IntPtr h, byte[] blob, UIntPtr size);
  [DllImport(@"$dir\PawnIOLib.dll", CharSet=CharSet.Ansi)] public static extern int pawnio_execute(IntPtr h, string name, ulong[] inb, UIntPtr insz, ulong[] outb, UIntPtr outsz, out UIntPtr ret);
  [DllImport(@"$dir\PawnIOLib.dll")] public static extern int pawnio_close(IntPtr h);
  public static int X(IntPtr h, string n, ulong[] i, ulong[] o) { UIntPtr r; return pawnio_execute(h, n, i, (UIntPtr)i.Length, o, (UIntPtr)o.Length, out r); }
  public static int WriteByte(IntPtr h, ulong addr, ulong cmd, ulong val) { return X(h, "ioctl_smbus_xfer", new ulong[] { addr, 0, cmd, 2, val, 0, 0, 0, 0 }, new ulong[5]); }
}
"@
$blob = [IO.File]::ReadAllBytes("$dir\SmbusPIIX4.bin")
$mtx = New-Object System.Threading.Mutex($false, 'Global\Access_SMBUS.HTP.Method')
$h = [IntPtr]::Zero
[void][P]::pawnio_open([ref]$h)
[void][P]::pawnio_load($h, $blob, [UIntPtr][uint64]$blob.Length)
$last = $null; $writes = 0
"RAM writer running at $Hz Hz. Ctrl+C to stop."
try {
  while ($true) {
    Start-Sleep -Milliseconds (1000 / $Hz)
    try { $rgb = [IO.File]::ReadAllBytes($frame) } catch { continue }
    if ($rgb.Length -lt 15) { continue }
    $key = [BitConverter]::ToString($rgb)
    if ($key -eq $last) { continue }
    $last = $key
    if (-not $mtx.WaitOne(200)) { continue }
    try {
      [void][P]::X($h, 'ioctl_piix4_port_sel', [uint64[]]@(0), (New-Object uint64[] 1))
      foreach ($led in 0..4) {
        $o = $led * 3
        [void][P]::WriteByte($h, 0x77, 0x17 + $led, $rgb[$o])
        [void][P]::WriteByte($h, 0x77, $rgb[$o + 1], $rgb[$o + 2])
      }
    } finally { $mtx.ReleaseMutex() }
    $writes++
    if ($writes % 100 -eq 0) { "$writes updates, last $key" }
  }
} finally { [void][P]::pawnio_close($h) }
