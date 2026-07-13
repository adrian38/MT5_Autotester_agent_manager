$bytes = New-Object byte[] 32
$rng = New-Object Security.Cryptography.RNGCryptoServiceProvider
try {
    $rng.GetBytes($bytes)
} finally {
    $rng.Dispose()
}
([BitConverter]::ToString($bytes) -replace '-', '').ToLowerInvariant()
