# Releasing zero-api

Releases are built and published by the [`Release` workflow](.github/workflows/release.yml). Publishing a GitHub Release triggers it; a manual dispatch can publish the same artifacts to TestPyPI first.

| Trigger | Publishes to | Attaches assets to the GitHub Release |
|---|---|---|
| GitHub Release published | PyPI | Yes |
| `workflow_dispatch`, `target=pypi` | PyPI | No |
| `workflow_dispatch`, `target=testpypi` | TestPyPI | No |

The workflow never edits the repository. The version in `pyproject.toml` is the source of truth, and the git tag must match it exactly (`v0.1.0` for version `0.1.0`).

## What the workflow does

1. Checks out the tagged commit and installs the locked dependency set with `uv sync --locked`.
2. Fails unless the tag is `v<project.version>`.
3. Runs `uv run mypy .` and the offline test suite (`uv run pytest`).
4. Builds the sdist and wheel with `uv build`, checks their metadata with `twine check --strict`, and smoke-tests the wheel in a clean virtual environment (including the `py.typed` marker).
5. Uploads the distributions as a workflow artifact.
6. In a separate job with `id-token: write`, publishes them to PyPI through OIDC trusted publishing — no PyPI token or password is stored in GitHub. The build job has no publishing permission, and the publish job does not check out or run repository code.
7. On a GitHub Release, uploads the exact same files to the release as assets.

The `pypa/gh-action-pypi-publish` action also generates and uploads [PEP 740 attestations](https://peps.python.org/pep-0740/) for the distributions.

## One-time setup

### 1. PyPI trusted publisher

The project does not exist on PyPI yet, so add a **pending trusted publisher** before the first release:

1. Sign in to <https://pypi.org>.
2. Open <https://pypi.org/manage/account/publishing/>.
3. Add a pending publisher with exactly these values:

| Field | Value |
|---|---|
| PyPI project name | `zero-api` |
| Owner | `wtfuii` |
| Repository name | `zero-api` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

The workflow name is the file name only, not the full path. If the project name is claimed by someone else before the first upload, the release cannot use `zero-api` without renaming the distribution.

### 2. TestPyPI trusted publisher (optional)

Repeat the pending-publisher setup at <https://test.pypi.org/manage/account/publishing/> with the same values except:

| Field | Value |
|---|---|
| Environment name | `testpypi` |

### 3. GitHub environments

In the repository, open **Settings → Environments** and create:

- `pypi` — strongly recommended: add yourself as a **required reviewer**, so a release waits for manual approval before publishing.
- `testpypi` — only needed for manual TestPyPI runs.

The environment names must match the PyPI trusted-publisher configuration and the workflow exactly.

If you add deployment branch or tag rules to the `pypi` environment, allow the release tags (for example `v*`): the publish job runs on the tag ref.

No `PYPI_API_TOKEN` or other publishing secret is needed.

### 4. License

The project is MIT-licensed. `LICENSE` holds the text and `pyproject.toml` declares it via the SPDX expression (`license = "MIT"`, `license-files = ["LICENSE"]`), so the wheel ships the file under `dist-info/licenses/` and PyPI displays the license. Nothing to do per release unless the copyright holder changes.

## Cut a release

Merge this release workflow to the default branch first: GitHub evaluates `release` event workflows from the default branch, so it must be on `main` before the first release.

The version is currently `0.1.0`, so the first tag is `v0.1.0`. For later releases, let uv bump the version (this updates both `pyproject.toml` and `uv.lock`):

```bash
# 1. Bump and review. Use --bump minor or --bump major when appropriate.
uv version --bump patch
git diff -- pyproject.toml uv.lock

# 2. Run the same checks the workflow runs, then commit and tag.
uv sync --locked
uv run mypy .
uv run pytest
git add pyproject.toml uv.lock
git commit -m "Release v0.1.1"
git tag -a v0.1.1 -m "zero-api v0.1.1"
git push origin main --follow-tags

# 3. Publish the GitHub Release. This triggers the workflow.
gh release create v0.1.1 --generate-notes --verify-tag
```

For the first release, use the already-committed version and tag `v0.1.0` instead of bumping:

```bash
git tag -a v0.1.0 -m "zero-api v0.1.0"
git push origin main --follow-tags
gh release create v0.1.0 --generate-notes --verify-tag
```

You can also create the release in the GitHub UI. A **draft** release does not trigger the workflow; it runs when the draft is published. Watch the run with `gh run watch` or in the **Actions** tab.

If the tag does not match `project.version`, the workflow fails before building and tells you to bump and retag.

## Test the pipeline with TestPyPI

Validate the packaging and trusted-publisher setup without touching PyPI:

```bash
gh workflow run release.yml --ref main -f tag=v0.1.0 -f target=testpypi
gh run list --workflow=release.yml --limit=1
```

Notes:

- A GitHub pre-release is still a published release and targets PyPI. Use the TestPyPI dispatch when you want a dry run.
- The tag must already exist and its version must match `pyproject.toml`.
- TestPyPI does not allow reusing a version. If `0.1.0` was already uploaded there, bump to a pre-release such as `0.2.0rc1`, commit, tag, and run again.
- The TestPyPI path does not attach assets to a GitHub Release.

To install a TestPyPI build, pull dependencies from PyPI as well:

```bash
uv pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  zero-api==0.1.0
```

## Recovery

- **Retry the build only:** re-run the failed workflow jobs. Publishing runs only after the build succeeds.
- **PyPI succeeded but release assets are missing:** re-run only the **Attach distributions to GitHub Release** job.
- **Manual PyPI publish:** if the release event did not trigger the workflow (for example, the release was created before this workflow existed), run:

  ```bash
  gh workflow run release.yml --ref main -f tag=v0.1.0 -f target=pypi
  ```

- **Version already exists on PyPI:** PyPI does not allow replacing files. Bump the version, commit, tag, and publish a new release. `skip-existing` only makes retries skip files that were already uploaded; it does not permit changing the contents of a published version.

## Troubleshooting

| Symptom | Check |
|---|---|
| `invalid-publisher` / OIDC error | Owner, repository, workflow name (`release.yml`), and environment must match the PyPI pending publisher exactly. |
| Workflow waiting for deployment | Approve the `pypi` environment deployment in the Actions tab. |
| Tag/version mismatch | Run `uv version --bump patch`, commit the lockfile too, push, delete the old tag, and create a new tag. Do not move a tag that has already been released. |
| Build job passes, publish job fails | The build artifact is still available for 7 days; re-run the publish job after fixing the trusted publisher or approval. |
| `File already exists` on upload | The version was already published. Bump the version; do not try to overwrite it. |
