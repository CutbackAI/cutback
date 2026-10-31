# Encrypted object retention and uncertain writes

Database retention and encrypted-object retention are separate. A crash can
leave a valid encrypted object before its database pointer commits. A database
snapshot cannot prove that an unexpired object is safe to delete: another
transaction or an uncertain commit acknowledgement may still own it.

The worker therefore preserves unexpired uncertain objects. Its physical expiry
sweep authenticates AES-GCM ownership, object identity and the original expiry,
then removes expired ciphertext even when no database pointer exists. Ordinary
content reads still reject expired objects. Cleanup never recreates content,
replays an inference operation or changes a dataset/plan/call receipt.

## File backend

Each page visits at most 100 directory entries, including skipped entries, with
a cooperative two-second work bound. Traversal is lazy and resumes in memory;
it neither sorts nor loads the complete store. Incomplete pages resume after
five seconds. A completed cycle waits an hour; a failed phase retries after a
minute. Hourly database maintenance and inference jobs have separate scheduling.
Restarting the worker starts a new traversal; this is not a strict erasure SLA.

Only canonical project/object UUID paths and owned rekey temporary filenames
are eligible. Symlinks and unrelated paths are skipped. Authentication failure,
an unknown encryption key, oversized ciphertext or a changed file preserves the
object and increments a failure count. No path, reference, credential or content
appears in the receipt. File ownership and permissions remain a host boundary.

## S3 backend

The sweep checks bucket versioning and uses bounded object/version pages. It
authenticates each exact version and deletes it only after expiry, with the
read object's ETag as a mandatory `IfMatch` precondition. In versioned or
suspended buckets, it supplies the exact version ID, including `null` versions.
Old expired versions are eligible; live versions remain. Delete markers remain
because removing one could expose a live older version.

This needs bucket-versioning and list access, plus read/delete-version access
when versioning is enabled. The SDK and S3-compatible service must support
conditional version deletion. A missing permission, unsupported conditional
request, retention lock, invalid cursor or changed version preserves content
and fails the phase. There is no unconditional-delete fallback. Network calls
retain the driver's finite timeouts and disabled retries; the work deadline is
cooperative between calls, not a hard network latency guarantee.

Qualification uses synthetic S3 protocol responses and the installed boto3
1.43.118 service model. An authorized real-bucket IAM, versioning, object-lock
and lifecycle drill is still required before claiming that environment's
physical erasure behavior. The tests do not contact a cloud bucket.

## Operator checks

`encrypted_object_expiry` is independent from ordinary payload/cache metadata
expiry and inference enablement. Inspect its `status`, `scanned`, `deleted`,
`failures` and `cycle_complete` counts. Unsupported custom storage backends
report `UNSUPPORTED`; they are not silently considered clean. The current
worker emits a structured count-only receipt on stdout for each due page.
Custom driver fields are excluded and malformed counters remain unknown.
Durability and monitoring of those logs depend on the self-hosted logging
configuration; this is not a separate durable audit-store claim.

Retain previous encryption keys until their retained content and the expiry
cycle have been reconciled. Unknown-key or corrupt files require operator
review. Backups, replicas, remote version copies, delete markers and external
storage policies require their own lifecycle controls. A completed primary
store sweep does not prove deletion from all of those locations.
