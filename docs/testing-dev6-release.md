# Test the dev6 development release

This walkthrough targets `0.1.0.dev6`. Check [PyPI](https://pypi.org/project/cutback/)
for publication availability. The release remains pre-alpha; no synthetic
confirmation proves customer quality, savings or production readiness.

Use Python 3.11–3.13, Git and Docker Desktop with Linux containers. Create a fresh
virtual environment outside an application or another qualification environment.

```console
python -m pip install "cutback[server,openai]==0.1.0.dev6"
cutback --version
cutback demo --source-policy
```

1. Open the printed **Ready** address and choose **Explore sample**.
2. Choose **Follow the one-budget improvement**. Inspect simple alternatives,
   conditional routing, separate confirmation and the exact checked source diff.
3. Check the deliberately unsafe alternative: it must fail the required priority
   criterion even if its modeled cost is lower.
4. Inspect the base/proposed application checks and inverse rollback. The sample
   does not deploy an application or borrow provider credentials.
5. Open **Costs → Saved charts** and `support-cost-and-reliability`. Inspect exact
   amounts, missing coverage and the underlying table; unknown values remain unknown.
6. Reload and reconnect through **Explore sample**. Credentials stay out of the
   URL, cookies and browser local/session storage.
7. Press **Ctrl+C** to stop the sample and its owned temporary database. Your other
   projects and containers are not part of this sample.

For a separate observed-workload journey, run `cutback demo --workload-portfolio`.
Follow [the detailed checks](testing-the-sample.md#test-the-complete-working-source-journey-first).

For offline discovery without Docker, install `cutback==0.1.0.dev6`, run
`cutback sample support-desk`, then `cutback scan support-desk`. Source scanning
never imports the application; costs remain unknown without observations.

The source distribution contains the independent
[shared-workflow walkthrough](../examples/workflow-checks/README.md). Its equivalent
change should pass, while deliberately broken shared code and prompt changes
should fail on the proposed tree and preserve the base/unchanged guard results.

A real collector deployment requires an operator-selected host, PostgreSQL,
HTTPS, secret delivery, backups and staging acceptance. Publishing the Python
package does not provision that infrastructure. Keep payload capture and paid
inference disabled unless separately scoped and authorized.
