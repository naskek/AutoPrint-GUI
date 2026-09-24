[CmdletBinding()]
param(
    [string]$QueueName = 'AutoPrint_TEST_Printronix',
    [string]$PortName = 'AUTOPRINT_TEST_SINK'
)

$ErrorActionPreference = 'Stop'

if ($QueueName -notmatch 'TEST') {
    throw "Unsafe QueueName '$QueueName': name must contain TEST."
}
if ($PortName -notmatch 'AUTOPRINT_TEST') {
    throw "Unsafe PortName '$PortName': port must contain AUTOPRINT_TEST."
}

$queue = Get-Printer -Name $QueueName -ErrorAction SilentlyContinue
if ($queue) {
    if ($queue.PortName -ne $PortName) {
        throw "Refusing cleanup: queue '$QueueName' uses unexpected port '$($queue.PortName)'."
    }

    $jobs = @(Get-PrintJob -PrinterName $QueueName -ErrorAction SilentlyContinue)
    if ($jobs.Count -gt 0) {
        $jobs | Remove-PrintJob -ErrorAction Stop
        Write-Host "Deleted $($jobs.Count) test spool job(s)."
    }

    Remove-Printer -Name $QueueName
    Write-Host "Removed TEST queue: $QueueName"
}

Start-Sleep -Milliseconds 300

$port = Get-PrinterPort -Name $PortName -ErrorAction SilentlyContinue
if ($port) {
    $remainingUsers = @(
        Get-Printer |
            Where-Object { $_.PortName -eq $PortName }
    )
    if ($remainingUsers.Count -gt 0) {
        throw "Refusing port cleanup: $PortName is still used by $($remainingUsers.Count) printer queue(s)."
    }

    Remove-PrinterPort -Name $PortName
    Write-Host "Removed TEST port: $PortName"
}

if (Get-Printer -Name $QueueName -ErrorAction SilentlyContinue) {
    throw "TEST queue still exists after cleanup: $QueueName"
}
if (Get-PrinterPort -Name $PortName -ErrorAction SilentlyContinue) {
    throw "TEST port still exists after cleanup: $PortName"
}

Write-Host "BarTender spooler TEST resources removed."
