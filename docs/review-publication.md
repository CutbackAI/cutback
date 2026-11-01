# Publish the checked change as a draft PR

Source-checkout feature under qualification. Published PyPI dev5 does not contain
this command. See [acceptance](progress.md) before using a release.

An application agent can publish the exact campaign change after independent
base/proposed application checks. The default is a local preview. Explicit
`--publish` uploads the checked source blob and commit to the named GitHub
repository and opens a draft pull request. It does not push the local Git
database, merge the change or deploy it.

The independent [campaign outcome sample](../examples/improvement-campaign/README.md)
includes the default preview after its actual synthetic confirmation and both-tree
native application checks. Run its documented `sample.py --outcomes` path against
your owned local collector. `pull_request_preview` shows the frozen source/commit
and review hashes for `example-owner/task-app`; this is an illustrative destination,
not a live repository. The sample makes no GitHub request and needs no GitHub key.

## Prepare the review

Complete [campaign confirmation and application review](improvement-campaigns.md).
The automatic [campaign handoff](campaign-handoff.md) can prepare both private
source repositories, execute authorized checks and save the exact review inputs
for publication. Existing separately managed workspaces remain supported.
The proposed checkout must contain exactly one unsigned commit directly above
the checked base commit. That base must already exist at the named GitHub base
branch. The one changed file, its executable mode and both source trees must
match the reviewed contract. The committed bytes must match the checked bytes;
if checkout line-ending conversion changed them, prepare matching workspaces and
repeat the checks. Additional ancestors, merge/signed commits, extra changed
files, mode changes and files over one MiB are refused by this bounded adapter.

The proposed commit's author, committer, message and changed source become
repository content. Inspect them with `git show PROPOSED_COMMIT` before
publication. Evaluation examples, raw responses, check output, local refs and
unrelated local history are not publication inputs.

Run `campaign review` again if the review predates saved publication inputs.
Its ignored `.cutback/agent/campaign.json` stores the local contract, receipt,
coverage declaration and workspace locations so publication can reverify them.
They contain no GitHub/provider token or application check output.

## Preview, then explicitly publish

From the application workspace, with its existing collector connection:

```sh
cutback campaign publish --repository YOUR_OWNER/YOUR_REPOSITORY \
  --base-branch main --head-branch cutback/reviewed-improvement \
  --purpose-reference application-pr-review
```

Expect `PREVIEW_ONLY_EXPLICIT_PUBLICATION_REQUIRED`, the exact base/proposed
commit and application-check hashes, and `remote_published: false`. Preview
does not need a GitHub token and makes no GitHub request. It refreshes the
current collector confirmation and rechecks both application trees; it makes
no model request.

Supply a repository-scoped GitHub credential through the process environment
as `CUTBACK_GITHUB_TOKEN`, then repeat the exact command with `--publish`.
Never put its value in a command argument, source file, receipt or report. This
adapter uses GitHub's [Git object API](https://docs.github.com/en/rest/git/commits#create-a-commit)
and [draft PR API](https://docs.github.com/en/rest/pulls/pulls#create-a-pull-request).
The credential needs Contents write and Pull requests write for that repository;
existing organization policies and branch protections still apply. There is no
arbitrary-host, fork, force-push, branch-update, PR-edit or merge operation.

The successful result contains the PR URL, exact commits, whether it is still a
draft, and `merge_authorized: false`. The PR body exposes only review/evidence
scope and hashes. Its source diff and Git commit metadata are visible according
to the target repository's permissions. A synthetic confirmation remains
synthetic in the draft; publication does not upgrade it to customer evidence.

## Resume an interrupted publication

Repeat the same command, repository and branch names. The private
`.cutback/agent/campaign-publication.json` journal freezes the intent and records
each mutation before sending it. Recovery reads the expected Git object IDs,
exact branch and matching PR. It does not repeat an acknowledged mutation or
update a different branch/PR. An existing ready-for-review PR can be reported
after its owner changes its draft state; the command does not make it ready.

If a previously dispatched mutation has no observable matching result, the
command stops with `REVIEW_PUBLICATION_UNCERTAIN_MUTATION_REQUIRES_RECONCILIATION`.
Inspect GitHub and the journal before deciding how to reconcile it. Deleting
the journal and blindly retrying can defeat this protection. Closed/moved PRs,
changed base/head branches and a changed local review also stop. Publication
does not rebase or rerun application checks on an unreviewed tree.

A branch can move after a completed read. The final receipt describes only the
exact heads checked at publication, not a permanent remote lock. Run the
application's required CI against its current PR head before review and merge.
There are no automatic remote retries; transport calls have finite time and
response bounds, use only `api.github.com`, refuse redirects and ignore ambient
proxy settings. The GitHub token stays in memory and is closed with the client.
