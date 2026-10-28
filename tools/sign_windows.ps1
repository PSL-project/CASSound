param([Parameter(Mandatory=$true)][string]$CertificateThumbprint,[Parameter(Mandatory=$true)][string]$File)
$ErrorActionPreference='Stop'
signtool sign /sha1 $CertificateThumbprint /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $File
if ($LASTEXITCODE -ne 0) { throw 'Signing failed' }
signtool verify /pa $File
if ($LASTEXITCODE -ne 0) { throw 'Signature verification failed' }
