[CmdletBinding()]
param(
    [string]$QueueName = 'AutoPrint_TEST_Printronix',
    [string]$PortName = 'AUTOPRINT_TEST_SINK',
    [string]$DriverPattern = 'Printronix'
)

$ErrorActionPreference = 'Stop'

if ($QueueName -notmatch 'TEST') {
    throw "Unsafe QueueName '$QueueName': name must contain TEST."
}
if ($PortName -notmatch 'AUTOPRINT_TEST') {
    throw "Unsafe PortName '$PortName': port must contain AUTOPRINT_TEST."
}

$drivers = @(
    Get-Printer |
        Where-Object { $_.DriverName -match $DriverPattern } |
        Select-Object -ExpandProperty DriverName -Unique
)

if ($drivers.Count -ne 1) {
    $drivers | ForEach-Object { Write-Host "Matched driver: $_" }
    throw "Expected exactly one driver matching '$DriverPattern', found $($drivers.Count)."
}

$driver = $drivers[0]
Write-Host "Using driver: $driver"

$port = Get-PrinterPort -Name $PortName -ErrorAction SilentlyContinue
if (-not $port) {
    # Local sink port: no TCP/IP host is configured, so this queue cannot reach
    # a physical network printer even if someone later resumes it accidentally.
    Add-PrinterPort -Name $PortName
    $port = Get-PrinterPort -Name $PortName
}

Write-Host "TEST port: $($port.Name)"

$queue = Get-Printer -Name $QueueName -ErrorAction SilentlyContinue
if (-not $queue) {
    Add-Printer -Name $QueueName -DriverName $driver -PortName $PortName
    $queue = Get-Printer -Name $QueueName
}

if ($queue.PortName -ne $PortName) {
    throw "Unsafe queue port: expected '$PortName', got '$($queue.PortName)'."
}
if ($queue.DriverName -ne $driver) {
    throw "Unsafe queue driver: expected '$driver', got '$($queue.DriverName)'."
}

$escapedName = $QueueName.Replace("'", "''")
$wmiPrinter = Get-CimInstance Win32_Printer -Filter "Name='$escapedName'"
if (-not $wmiPrinter) {
    throw "Could not resolve TEST queue through Win32_Printer."
}

$pauseResult = Invoke-CimMethod -InputObject $wmiPrinter -MethodName Pause
if ($pauseResult.ReturnValue -ne 0) {
    throw "Could not pause TEST queue. ReturnValue=$($pauseResult.ReturnValue)"
}

Start-Sleep -Milliseconds 500

$existingJobs = @(Get-PrintJob -PrinterName $QueueName -ErrorAction SilentlyContinue)
if ($existingJobs.Count -ne 0) {
    throw "TEST queue is not empty: $($existingJobs.Count) existing job(s)."
}

$queue = Get-Printer -Name $QueueName
Write-Host ""
Write-Host "TEST queue ready."
$queue | Select-Object Name, DriverName, PortName, PrinterStatus | Format-List
Write-Host "Queue is paused. No TCP/IP printer endpoint is configured."
