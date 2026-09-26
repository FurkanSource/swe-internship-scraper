# Release operations and recovery

## Candidate publication

1. Merge reviewed changes with green CI. Update `pyproject.toml` and
   `src/swe_scraper/__init__.py` to a fresh RC version, write the changelog, and
   set the matching tag/version in `.github/release-candidate.json`.
2. Verify the merged commit and create a signed immutable tag:

   ```sh
   git tag -s v1.0.0rc3 -m "SWE Internship Scraper 1.0.0rc3"
   git push origin v1.0.0rc3
   gh run list --workflow release.yml --limit 5
   ```

3. The release runs full reusable CI and security checks on that exact commit,
   then builds and inspects packages, generates an SBOM/checksums and provenance,
   publishes using the protected PyPI environment, and creates a GitHub release.
   Verify environment approval/protection settings on GitHub; workflow syntax
   alone does not establish who can approve a deployment.
4. Require the entire release run to pass, including exact-version PyPI install
   and rc2-to-candidate upgrade checks on Windows, Linux, and macOS. A failed
   post-publication test does not undo an upload: the candidate stays blocked.
5. Start both observations, then let the schedules collect subsequent evidence:

   ```sh
   gh workflow run candidate-stability.yml -f mode=daily
   gh workflow run candidate-stability.yml -f mode=weekly
   ```

Candidate stability checks out the configured tag. Artifacts record its commit,
version, run identity, UTC time, pagination completeness, and individual target
results. Reports are retained for 30 days. Daily checks use all 18 canaries;
weekly checks use ten catalog targets per core provider. Main-branch provider
health remains a separate signal and cannot satisfy candidate promotion.

## Stable promotion

Require seven consecutive healthy UTC dates, ending today or yesterday, with no
failed observation during that window, plus a successful recent weekly sample.
Repeated runs on one date count only once. Missing/expired evidence blocks
promotion. Fixing behavior requires a fresh RC and restarts observation.

1. Fetch the candidate tag and change only the two core version declarations to
   `1.0.0` and the changelog. Review and merge that metadata-only commit.
2. Check evidence before creating a stable tag (PowerShell shown):

   ```powershell
   git fetch origin --tags
   py scripts/release_stability.py --repo FurkanSource/swe-internship-scraper --ref refs/tags/v1.0.0
   ```

3. Only after success, sign/push `v1.0.0` and verify the entire release workflow.
   The workflow repeats the gate, including a comparison with the candidate's
   code. New behavior cannot inherit an old candidate's stability evidence.
4. Verify GitHub/PyPI artifacts and installation. Then update documentation to
   recommend stable installation without `--pre`. Migration of any private
   consumer happens only after this stable package is independently verified.

## Failed release or incorrect artifact

- Inspect the failure: `gh run view RUN_ID --log-failed`.
- Before any retry, inspect both the GitHub release and PyPI version. Do not
  assume an unsuccessful Actions run means nothing was published.
- If nothing was uploaded and failure was environmental, use
  `gh run rerun RUN_ID --failed`. Do not move the tag or rebuild different code
  under the same version.
- If PyPI upload succeeded but GitHub release creation failed, recover the
  existing `release-RUN_ID-ATTEMPT` Actions artifact. Verify `SHA256SUMS`, compare
  distribution hashes with PyPI, and verify provenance with
  `gh attestation verify ARTIFACT --repo FurkanSource/swe-internship-scraper`.
  Create the missing GitHub release from those exact files; never rebuild them.
- If a published package is defective, document the impact, yank that version
  through the PyPI project interface with a reason when warranted, and publish
  a fresh patch/RC. Yanking is not deletion and cannot replace an existing file.
  Never overwrite or force-move a published tag.
- Users can recover in a fresh environment by installing a verified earlier
  version: `py -m pip install "swe-internship-scraper==KNOWN_GOOD_VERSION"`.
  Substitute a version confirmed by its release checks; do not blindly treat
  an earlier version as safe. Back up watch state/output before changing versions.

## Optional plugin

The plugin has independent `icims-vVERSION` tags and a separate PyPI environment.
Core-only releases must not republish its existing version. The seven-day gate
described here applies to stable core releases; iCIMS remains experimental 0.x.
