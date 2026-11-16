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

foreach ($name in @('STORE_TENANT_ID', 'STORE_CLIENT_ID', 'STORE_CLIENT_SECRET')) {
    if (-not [Environment]::GetEnvironmentVariable($name)) {
        throw "Configure the GitHub Actions secret $name before connecting to Partner Center."
    }
}

$tenantId = [guid]::Parse($env:STORE_TENANT_ID)
$token = Invoke-RestMethod -Method Post -ContentType 'application/x-www-form-urlencoded' `
    -TimeoutSec 60 -Uri "https://login.microsoftonline.com/$tenantId/oauth2/token" -Body @{
        grant_type = 'client_credentials'
        client_id = $env:STORE_CLIENT_ID
        client_secret = $env:STORE_CLIENT_SECRET
        resource = 'https://manage.devcenter.microsoft.com'
    }
$api = @{ Headers = @{ Authorization = "Bearer $($token.access_token)" }; TimeoutSec = 60; MaximumRedirection = 0 }
$appUri = "https://manage.devcenter.microsoft.com/v1.0/my/applications/$productId"
$app = Invoke-RestMethod -Uri $appUri @api
if ($app.packageIdentityName -ne 'CrisprLabs.Cuvyro' -or
    $app.publisherName -ne 'CN=3E3BFB6E-6C9B-46C6-BFE3-23DEFC167689') {
    throw 'Partner Center returned an unexpected Cuvyro package identity.'
}

function Get-X64ReleaseVersions($submission) {
    foreach ($item in $submission.applicationPackages) {
        if ($item.architecture -eq 'x64' -and $item.fileStatus -ne 'PendingDelete' -and $item.version) {
            $packageVersion = [version]::Parse($item.version)
            # The Store reserves the fourth field.
            [version]::new($packageVersion.Major, $packageVersion.Minor, $packageVersion.Build)
        }
    }
}

$accepted = @('PreProcessing', 'Certification', 'Release', 'PendingPublication', 'Publishing', 'Published')

function Confirm-StoreCommit($submissionId, $status) {
    $statusUri = "$appUri/submissions/$submissionId/status"
    for ($attempt = 0; $status -eq 'CommitStarted' -and $attempt -lt 16; $attempt++) {
        Start-Sleep -Seconds 15
        $status = (Invoke-RestMethod -Uri $statusUri @api).status
    }
    if ($status -notin $accepted) {
        throw "Store submission $submissionId is $status. Check Partner Center before retrying."
    }
    "$status"
}

function Assert-StorePricing($submission) {
    # PUT replaces the submission. Sending empty or unknown pricing can change
    # the Store price, and the legacy API does not round-trip per-market Base pricing.
    if ($submission.pricing.priceId -notmatch '^(Free|NotAvailable|Tier[0-9]+)$') {
        throw 'Partner Center did not return a supported base price. Keep the existing pricing and submit through Partner Center.'
    }
}

$liveId = $app.lastPublishedApplicationSubmission.id
if (-not $liveId) { throw 'Partner Center has no live Cuvyro submission.' }
$live = Invoke-RestMethod -Uri "$appUri/submissions/$liveId" @api
$liveVersion = Get-X64ReleaseVersions $live | Sort-Object -Descending | Select-Object -First 1
if (-not $liveVersion) { throw 'Could not determine the live Cuvyro x64 package version.' }
if ($CheckAccess) {
    Assert-StorePricing $live
    Write-Host "Authenticated to Partner Center and verified Cuvyro x64 $liveVersion and its pricing configuration."
    return
}
$releaseVersion = [version]::new($version.Major, $version.Minor, $version.Build)
if ($releaseVersion -le $liveVersion) {
    Write-Host "Skipping Store update. Version $liveVersion is already live."
    return
}

$pendingId = $app.pendingApplicationSubmission.id
if ($pendingId) {
    $pending = Invoke-RestMethod -Uri "$appUri/submissions/$pendingId" @api
    $matchingPackage = $releaseVersion -in (Get-X64ReleaseVersions $pending) -or
        [bool]($pending.applicationPackages | Where-Object {
            $_.fileName -eq $package.Name -and $_.fileStatus -ne 'PendingDelete' -and -not $_.version
        })
    if (($pending.status -in $accepted -or $pending.status -eq 'CommitStarted') -and $matchingPackage) {
        $status = Confirm-StoreCommit $pendingId $pending.status
        Write-Host "Store submission $pendingId for $releaseVersion is already $status."
        return
    }
    throw "Partner Center already has submission $pendingId ($($pending.status)). Review it before retrying."
}

Assert-StorePricing $live
$azcopy = Get-Command azcopy -ErrorAction Stop
$temp = New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) "cuvyro-store-$([guid]::NewGuid())")
try {
    # Create fails if another writer already opened a draft. Every later write
    # addresses the ID returned by this request, so it cannot replace that draft.
    $draft = Invoke-RestMethod -Uri "$appUri/submissions" -Method Post @api
    $draftId = [string]$draft.id
    if ($draftId -notmatch '^\d+$' -or $draft.status -ne 'PendingCommit') {
        throw 'Partner Center did not return a new pending draft. Review Partner Center before retrying.'
    }
    $draftUri = "$appUri/submissions/$draftId"
    Write-Host "Created Partner Center draft $draftId."
    Assert-StorePricing $draft
    if ($draft.pricing.priceId -ne $live.pricing.priceId) {
        throw "The base price changed while creating draft $draftId. Review Partner Center before retrying."
    }
    if (Get-X64ReleaseVersions $draft | Where-Object { $_ -ge $releaseVersion }) {
        throw "Draft $draftId already contains version $releaseVersion or newer. Review Partner Center before retrying."
    }

    $uploadUri = [uri]$draft.fileUploadUrl
    if ($uploadUri.Scheme -ne 'https' -or $uploadUri.Host -notlike '*.blob.core.windows.net' -or
        $uploadUri.UserInfo -or -not $uploadUri.Query) {
        throw "Partner Center returned an unexpected upload destination for draft $draftId."
    }

    $draft.applicationPackages = @(
        foreach ($item in $draft.applicationPackages) {
            if ($item.architecture -eq 'x64') { $item.fileStatus = 'PendingDelete' }
            $item
        }
        @{
            fileName = $package.Name
            fileStatus = 'PendingUpload'
            minimumDirectXVersion = 'None'
            minimumSystemRam = 'None'
        }
    )

    $archivePath = Join-Path $temp.FullName 'upload.zip'
    $archive = [IO.Compression.ZipFile]::Open($archivePath, [IO.Compression.ZipArchiveMode]::Create)
    try {
        $null = [IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $archive, $package.FullName, $package.Name, [IO.Compression.CompressionLevel]::NoCompression
        )
        foreach ($listing in $draft.listings.PSObject.Properties.Value) {
            $variants = @($listing.baseListing) + @($listing.platformOverrides.PSObject.Properties.Value)
            foreach ($image in $variants.images | Where-Object { $_.fileStatus -eq 'PendingUpload' }) {
                $imageUri = [uri]$image.fileName
                if ($imageUri.Scheme -ne 'https' -or $imageUri.UserInfo) {
                    throw "Draft $draftId requests a listing image with no HTTPS download URL."
                }
                $name = "$([guid]::NewGuid())$([IO.Path]::GetExtension($imageUri.AbsolutePath))"
                $imagePath = Join-Path $temp.FullName $name
                Invoke-WebRequest -Uri $imageUri -OutFile $imagePath -TimeoutSec 60
                $null = [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $imagePath, $name)
                $image.fileName = $name
            }
        }
    } finally {
        $archive.Dispose()
    }

    foreach ($field in @('id', 'status', 'statusDetails', 'fileUploadUrl', 'friendlyName')) {
        $draft.PSObject.Properties.Remove($field)
    }
    $updated = Invoke-RestMethod -Uri $draftUri -Method Put -ContentType 'application/json' `
        -Body ($draft | ConvertTo-Json -Depth 100 -Compress) @api
    if ($updated.id -ne $draftId -or $updated.status -ne 'PendingCommit' -or
        -not ($updated.applicationPackages | Where-Object {
            $_.fileName -eq $package.Name -and $_.fileStatus -eq 'PendingUpload'
        })) {
        throw "Partner Center did not stage the expected MSIX on draft $draftId."
    }

    # Use block uploads, including for SAS URLs signed with older Storage versions.
    # Suppress uploader output because the signed URL is a temporary credential.
    $uploadUrl = $uploadUri.AbsoluteUri.Replace('+', '%2B')
    try {
        & $azcopy copy $archivePath $uploadUrl --from-to=LocalBlob --content-type=application/zip `
            --block-size-mb=8 --put-blob-size-mb=8 --log-level=NONE *> $null
        if ($LASTEXITCODE -ne 0) { throw 'AzCopy returned an error.' }
    } catch {
        throw "Package upload failed for Store draft $draftId. Review Partner Center before retrying."
    }

    $commit = Invoke-RestMethod -Uri "$draftUri/commit" -Method Post @api
    $status = Confirm-StoreCommit $draftId $commit.status
    Write-Host "Store submission $draftId accepted with status $status. Package: $($package.Name) (SHA-256 $sha256)."
} finally {
    Remove-Item -LiteralPath $temp.FullName -Recurse -Force
}
