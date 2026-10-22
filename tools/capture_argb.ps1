# Records all USB traffic on every root hub for a fixed window, so the Razer ARGB box's
# enumeration (descriptors) and Synapse's startup conversation with it are captured.
# Run elevated. Output: one .pcap per hub next to this script.
param([int]$Seconds = 240)

$out = $PSScriptRoot
$exe = 'C:\Program Files\USBPcap\USBPcapCMD.exe'
$procs = foreach ($n in 1..3) {
    Start-Process $exe -ArgumentList "-d \\.\USBPcap$n -o `"$out\argb_hub$n.pcap`" -A --inject-descriptors -s 65535" -PassThru -WindowStyle Hidden
}
"Capturing for $Seconds s. Plug in the Razer ARGB box NOW, let Synapse find it, open its page, press any LED-count reset/rescan, then open Chroma Studio."
for ($i = $Seconds; $i -gt 0; $i -= 10) { "  $i s left"; Start-Sleep 10 }
$procs | Stop-Process -Force -ErrorAction SilentlyContinue
Get-ChildItem "$out\argb_hub*.pcap" | Select-Object Name, Length | Format-Table
"Done. You can close this window."
Read-Host 'Press Enter to close'
