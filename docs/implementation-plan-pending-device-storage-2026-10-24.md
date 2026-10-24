# Pending device storage

Store at most 256 prepared choices in a separate table. A choice cannot affect
active devices or network policy. Use the existing MAC and display name
normalisers. A saved profile must exist and cannot disappear while referenced.
The main risk is compatibility with settings replacement and older repository
code, which must be proved with real temporary databases before publication.

This is Stage 1 groundwork only. It adds no owner workflow or activation path.
The owner has authorised implementation following the reviewed persistence plan
at 92eabdd682459678f9a139824d961f2f3e2ff4eb. No open design questions block this
bounded slice.

- Add an immutable validated PendingDeviceChoice with explicit MAC, name,
  exclusive action/profile, allowed source, optional observed time, local saved
  time and positive integer revision. Times use whole Unix seconds from zero to
  the final second of year 9999. Caller verification and IP fields are absent.
- Expose list/get/save/delete methods on ConfigRepository. Save accepts a choice
  plus expected_revision, with None requiring absence. Return the stored choice
  with a fresh SQLite revision. Delete requires a positive expected revision. Validate
  inputs before writing and check existence, profile, revision and cap inside
  BEGIN IMMEDIATE. Failures restore the whole transaction.
- Create one additive table with a unique MAC logical key, SQLite AUTOINCREMENT
  revision primary key and a deferred NO ACTION profile reference. Each save
  allocates a fresh durable revision so stale edits cannot target a deleted then
  recreated choice. Only SQLite internal sequence metadata changes alongside the
  pending rows. Include pending references in profile_references. Retain choices
  through settings replacement and SQLite backup without changing exports.
- Add focused tests for invalid inputs, revision conflicts, capacity races,
  rollback, profile references, settings import, backup and repeated reopening.
  Compare all existing tables before and after pending operations.
- Load the actual 3f5499a9 repository source into a temporary probe package.
  Prove foreign keys stay enabled, unrelated old writes retain pending rows,
  retained-profile imports succeed and omitted-profile imports or deletion fail
  atomically. Record exact source hash, commands and results separately.
- Run focused native wrapper tests plus existing storage/import/profile tests.
  Use a private copy of cached Python 3.12 dependencies. No network, real router,
  helper, browser, Docker or full-suite run is needed.

Risk class B by the repository template because this adds dashboard persistence
without network changes. Household risk is low because the table is inert.
Installation uses the existing automatic schema initialisation. No backfill or
manual migration is needed. Older code must preserve pending data and may report
a generic profile-in-use error. Remove pending references before such profile
deletion. Whole-database rollback restores a backup and never promotes choices.
