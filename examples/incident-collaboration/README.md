# Independent incident collaboration sample

This source-checkout example posts two synthetic comment revisions to an
explicitly selected local incident, reads them through purpose-scoped requests,
then erases their encrypted text. It never calls a model or configures a
notification. It does not use a private reference application.

1. Start the matching local PostgreSQL collector with migration `0036` and
   explicitly configured encrypted storage. Use a separate synthetic project.
2. Run the [online monitoring example](../online-monitoring/README.md), let its
   enabled metadata worker produce an incident, and open that incident in
   **Monitoring**. Its technical failure does not establish task-quality loss.
3. Open **Authorize encrypted team comments**. Choose synthetic rights, a short
   retention period and an authorization reference. This explicitly permits
   comment reads by current project readers, separately from captured trace text.
4. Keep `CUTBACK_ENDPOINT`, `CUTBACK_PROJECT_ID`, `CUTBACK_CONTROL_KEY`,
   `CUTBACK_INCIDENT_ID` and `CUTBACK_COMMENT_POLICY_ID` in the local process
   environment. The incident and policy IDs come from their scoped API metadata;
   the endpoint must use a numeric loopback address. Never put keys in a command,
   shared URL, transcript or source file.
5. Preview, then execute from the source tool environment:

   ```sh
   python examples/incident-collaboration/sample.py --receipt .cutback/comment-example.json
   python examples/incident-collaboration/sample.py --receipt .cutback/comment-example.json --execute-local
   ```

The preview makes no network request and writes no journal. Execution saves only
destination/UUID/phase metadata in the private journal, with no key or comment
text. Repeating the exact execution after a lost reply uses the same immutable
version IDs; a completed journal does not recreate erased text. Changed
destinations, expired policies, lost permissions or altered comment state stop
the example. Keep the same journal when inspecting an incomplete result.

The expected result is `COMPLETED_SYNTHETIC_COMMENT_LIFECYCLE`, two revisions
before erasure and zero retained text revisions afterward. Open the incident's
discussion to inspect its tombstone and audit metadata. Delete the isolated
project through Data controls when finished. The example never deletes an
existing incident, changes production traffic or treats a comment as verified
application evidence. See [the product contract](../../docs/incident-comments.md).
