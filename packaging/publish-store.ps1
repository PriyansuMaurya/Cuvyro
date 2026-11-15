# Checks Store access or submits a package already verified by the build.
param([switch]$CheckAccess)

$ErrorActionPreference = 'Stop'
$productId = '9MX46V9WW83Q'
if (-not $CheckAccess) {
    $version = [version]::Parse($env:STORE_VERSION)
    $package = Get-Item -LiteralPath "dist/Cuvyro-$version-x64.msix"
    $sha256 = (Get-FileHash -LiteralPath $package.FullName -Algorithm SHA256).Hash
    if ($env:STORE_SHA256 -notmatch '^[a-fA-F0-9]{64}$' -or $sha256 -ne $env:STORE_SHA256) {
        throw 'The MSIX does not match the SHA-256 from the build job.'
    }
}

foreach ($name in @('STORE_TENANT_ID', 'STORE_CLIENT_ID', 'STORE_CLIENT_SECRET', 'STORE_SELLER_ID')) {
    if (-not [Environment]::GetEnvironmentVariable($name)) {
        throw "Configure the GitHub Actions secret $name before submitting to Partner Center."
    }
}

$tenantId = [guid]::Parse($env:STORE_TENANT_ID)
$token = Invoke-RestMethod -Method Post -ContentType 'application/x-www-form-urlencoded' `
    -Uri "https://login.microsoftonline.com/$tenantId/oauth2/token" -Body @{
        grant_type = 'client_credentials'
        client_id = $env:STORE_CLIENT_ID
        client_secret = $env:STORE_CLIENT_SECRET
        resource = 'https://manage.devcenter.microsoft.com'
    }
$headers = @{ Authorization = "Bearer $($token.access_token)" }
$appUri = "https://manage.devcenter.microsoft.com/v1.0/my/applications/$productId"
$app = Invoke-RestMethod -Uri $appUri -Headers $headers
if ($app.packageIdentityName -ne 'CrisprLabs.Cuvyro' -or
    $app.publisherName -ne 'CN=3E3BFB6E-6C9B-46C6-BFE3-23DEFC167689') {
    throw 'Partner Center returned an unexpected Cuvyro package identity.'
}

function Get-X64ReleaseVersions($submission) {
    foreach ($item in $submission.applicationPackages) {
        if ($item.architecture -eq 'x64' -and $item.fileStatus -ne 'PendingDelete') {
            $packageVersion = [version]::Parse($item.version)
            # The Store reserves the fourth field.
            [version]::new($packageVersion.Major, $packageVersion.Minor, $packageVersion.Build)
        }
    }
}

$liveId = $app.lastPublishedApplicationSubmission.id
if (-not $liveId) { throw 'Partner Center has no live Cuvyro submission.' }
$live = Invoke-RestMethod -Uri "$appUri/submissions/$liveId" -Headers $headers
$liveVersion = Get-X64ReleaseVersions $live | Sort-Object -Descending | Select-Object -First 1
if (-not $liveVersion) { throw 'Could not determine the live Cuvyro x64 package version.' }
if ($CheckAccess) {
    Write-Host "Authenticated to Partner Center and verified Cuvyro x64 $liveVersion."
    return
}
$releaseVersion = [version]::new($version.Major, $version.Minor, $version.Build)
if ($releaseVersion -le $liveVersion) {
    Write-Host "Skipping Store update. Version $liveVersion is already live."
    return
}

$pendingId = $app.pendingApplicationSubmission.id
if ($pendingId) {
    $pending = Invoke-RestMethod -Uri "$appUri/submissions/$pendingId" -Headers $headers
    $active = @('CommitStarted', 'PreProcessing', 'Certification', 'Release',
        'PendingPublication', 'Publishing', 'Published')
    if ($pending.status -in $active -and $releaseVersion -in (Get-X64ReleaseVersions $pending)) {
        Write-Host "Store submission $pendingId for $releaseVersion is already $($pending.status)."
        return
    }
    throw "Partner Center already has submission $pendingId ($($pending.status)). Review it before retrying."
}

& msstore reconfigure --tenantId $env:STORE_TENANT_ID --sellerId $env:STORE_SELLER_ID `
    --clientId $env:STORE_CLIENT_ID --clientSecret $env:STORE_CLIENT_SECRET
if ($LASTEXITCODE -ne 0) { throw 'Microsoft Store CLI authentication failed.' }

& msstore publish $package.FullName -id $productId
if ($LASTEXITCODE -ne 0) { throw 'MSIX submission failed. Review its status in Partner Center before retrying.' }
Write-Host "Submitted $($package.Name) (SHA-256 $sha256). Check Partner Center for certification and publication."
