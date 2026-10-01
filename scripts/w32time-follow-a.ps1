# [B, Windows host] Run once in an elevated (Administrator) PowerShell.
# Human-approved 2026-10-01 (CLAUDE.md §2 Environment Overrides): A serves time, B follows.
# Under WSL2 the Linux clock of Machine B is owned by WSL's system VM, which syncs it to the
# Windows host clock via the Hyper-V PTP clock (PHC0). So B follows A by making Windows follow A.
# Requires k8s/k3s/chrony-server.sh to have been run on A.
$ErrorActionPreference = "Stop"
$A_IP = "192.168.137.10"

w32tm /config /manualpeerlist:"$A_IP,0x8" /syncfromflags:manual /reliable:no /update
# Poll A every 2^6 = 64 s
reg add HKLM\SYSTEM\CurrentControlSet\Services\W32Time\Config /v MinPollInterval /t REG_DWORD /d 6 /f
reg add HKLM\SYSTEM\CurrentControlSet\Services\W32Time\Config /v MaxPollInterval /t REG_DWORD /d 6 /f
# Standalone-PC defaults re-evaluate the slew only about hourly (UpdateInterval), so after a 64 s
# poll the clock kept slewing at a fixed ~1.2 ms/s straight past A (observed 2026-10-01:
# -600 ms -> +590 ms). High-accuracy loop settings: update every 1 s (units of 1/100 s), faster
# frequency correction, and step instead of slewing when the error is over 1 s.
reg add HKLM\SYSTEM\CurrentControlSet\Services\W32Time\Config /v UpdateInterval /t REG_DWORD /d 100 /f
reg add HKLM\SYSTEM\CurrentControlSet\Services\W32Time\Config /v FrequencyCorrectRate /t REG_DWORD /d 2 /f
reg add HKLM\SYSTEM\CurrentControlSet\Services\W32Time\Config /v MaxAllowedPhaseOffset /t REG_DWORD /d 1 /f
Set-Service w32time -StartupType Automatic
Restart-Service w32time
w32tm /resync /force
w32tm /query /status
w32tm /stripchart /computer:$A_IP /samples:5 /dataonly
