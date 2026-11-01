# Incident comments

This source feature passed local component qualification and is absent from PyPI
dev5; exact-head hosted acceptance remains pending. The
[receipt](evidence/incident-comments-acceptance.json) records both installed SDK
envelopes, the independent HTTP-only client, authenticated browser/mobile/axe and
complete encrypted recovery, including retained failed trials. It adds a
bounded team discussion to retained incidents, separately from their technical
evidence and owner/state transitions. A comment is a human assertion, never gold
data, an independently verified outcome or an instruction for an automatic agent.

## Discuss an incident

Open **Monitoring**, choose an incident, then **Team discussion**. Listings show
only author references, timestamps, revisions and erasure metadata. Text is never
included in incident lists, comment lists, audit records or metadata exports.

An administrator first opens **Authorize encrypted team comments**, reviews the
rights and environment, and approves retention, expiry and a shared object cap.
This uses a separate capture-policy scope: `incident-comments`, local storage,
the incident's exact environment, and full sampling of deliberately posted
comments. It does not authorize trace-content reads, provider inference,
notifications or arbitrary application capture. Encrypted storage must also be
explicitly enabled and configured by the operator.

Editors can post comments and revise their own comments. Each revision is
immutable, separately encrypted and bound to its original author credential.
The UI preserves request/version IDs after an uncertain response; repeating the
same intent reconciles the canonical result. A stale revision cannot overwrite
a teammate's change. An editor with a different credential cannot impersonate
the original author. An administrator can moderate by erasing inappropriate
text; existing revisions cannot be silently rewritten.

Current project readers can supply a read purpose to open the current or a
retained historical revision. Each request rechecks project access, the current
policy, scope, expiry and encrypted object integrity, and writes a content-free
audit event. Refreshing the discussion hides previously read text. Revocation
cannot recall text someone already read; it stops subsequent reads. The browser
stores credentials and opened text in memory, with no local/session storage or
cookies. Comment text is rendered as text, never HTML or executable Markdown.

## Retention, deletion and limits

Each body expires at the earliest of its policy expiry, approved retention and
the project's raw retention limit. The existing payload-retention worker erases
expired encrypted objects. Retained version metadata cannot reconstruct their
text. A newer revision never renews an older revision's rights or expiry.

**Delete comment text** checks the reviewed current revision and erases every
retained body before reporting success. It remains available after capture is
revoked or the storage hard deny is enabled. Storage failure reports unconfirmed
erasure; it never reports success from a flag alone. A tombstone and audited
actor/revision remain. Audited project deletion erases comment objects and
removes their metadata. A lost database commit acknowledgment preserves any
canonically referenced encrypted blob; it is not treated as proof of rollback.

A comment has at most 50 text revisions and 8,000 characters per body, subject
also to policy byte bounds and redaction. An incident retains at most 200 comment
records; a project retains at most 10,000. Tombstones count toward those limits.
The shared capture quota also counts these encrypted revisions alongside other
approved objects and reservations. Listings use bounded chronological keyset
pages. The policy chooser examines at most 100 enabled policies and discloses
truncation. There is no full-text search, arbitrary attachment, automatic mention
notification or independent proof that two credentials belong to two humans.

Migration `0036` adds compound tenant/parent/payload constraints and revision/
tombstone checks. Earlier released migrations are unchanged. Downgrade refuses
populated comment metadata; export it and erase the isolated project before a
binary/schema rollback. The [independent sample](../examples/incident-collaboration/README.md)
exercises the ordinary APIs without a paid provider.
