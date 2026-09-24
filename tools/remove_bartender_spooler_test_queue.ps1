[CmdletBinding()]
param(
    [string]$QueueName = 'AutoPrint_TEST_Printronix',
    [string]$PortName = 'AUTOPRINT_TEST_SINK',
    [int]$ReleaseTimeoutSeconds = 15
)

$ErrorActionPreference = 'Stop'

if ($QueueName -notmatch 'TEST') {
    throw "Unsafe QueueName '$QueueName': name must contain TEST."
}
if ($PortName -notmatch 'AUTOPRINT_TEST') {
    throw "Unsafe PortName '$PortName': port must contain AUTOPRINT_TEST."
}

function Get-TestPortUsers {
    param([string]$Name)

    $printManagementUsers = @(
        Get-Printer -ErrorAction SilentlyContinue |
            Where-Object { $_.PortName -eq $Name }
    )

    $cimUsers = @(
        Get-CimInstance Win32_Printer -ErrorAction SilentlyContinue |
            Where-Object { $_.PortName -eq $Name }
    )

    return @($printManagementUsers + $cimUsers)
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

# Queue deletion is asynchronous inside the Windows Print Spooler. Wait until
# both PrintManagement and Win32_Printer stop reporting users of the test port.
$deadline = (Get-Date).AddSeconds($ReleaseTimeoutSeconds)
do {
    $queueStillExists = Get-Printer -Name $QueueName -ErrorAction SilentlyContinue
    $remainingUsers = @(Get-TestPortUsers -Name $PortName)

    if (-not $queueStillExists -and $remainingUsers.Count -eq 0) {
        break
    }

    Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $deadline)

if ($queueStillExists) {
    throw "TEST queue still exists after waiting for spooler release: $QueueName"
}

$remainingUsers = @(Get-TestPortUsers -Name $PortName)
if ($remainingUsers.Count -gt 0) {
    $names = ($remainingUsers | Select-Object -ExpandProperty Name -Unique) -join ', '
    throw "Refusing port cleanup: $PortName is still reported in use by: $names"
}

$port = Get-PrinterPort -Name $PortName -ErrorAction SilentlyContinue
if ($port) {
    $lastError = $null
    for ($attempt = 1; $attempt -le 10; $attempt++) {
        try {
            Remove-PrinterPort -Name $PortName -ErrorAction Stop
            $lastError = $null
            break
        }
        catch {
            $lastError = $_

            # Never retry if a printer starts using the port while cleanup waits.
            $remainingUsers = @(Get-TestPortUsers -Name $PortName)
            if ($remainingUsers.Count -gt 0) {
                $names = ($remainingUsers | Select-Object -ExpandProperty Name -Unique) -join ', '
                throw "Refusing port cleanup: $PortName became used by: $names"
            }

            Start-Sleep -Milliseconds 750
        }
    }

    if ($lastError) {
        throw (
            "TEST port is still busy after spooler-release retries. " +
            "No production queue was modified. Re-run this cleanup helper later. " +
            "Last error: " + $lastError.Exception.Message
        )
    }

    Write-Host "Removed TEST port: $PortName"
}

if (Get-Printer -Name $QueueName -ErrorAction SilentlyContinue) {
    throw "TEST queue still exists after cleanup: $QueueName"
}
if (Get-PrinterPort -Name $PortName -ErrorAction SilentlyContinue) {
    throw "TEST port still exists after cleanup: $PortName"
}

Write-Host "BarTender spooler TEST resources removed."
