# briar-cli container image.
#
# Built in CI from the wheel produced by `uv build` (see .github/workflows/
# release.yml), so the image always matches the version published to PyPI.
#
#   docker run --rm iklob1/briar --version
FROM python:3.11-slim

# Install the wheel CI just built. The glob resolves to the single versioned
# wheel in dist/. (The MCP registry entry points at the 1.x images; 2.0 has no
# `briar mcp serve` and no `mcp` extra.)
COPY dist/ /tmp/dist/
RUN whl="$(ls /tmp/dist/*.whl)" && pip install --no-cache-dir "${whl}" && rm -rf /tmp/dist

# bubblewrap and socat: `briar serve` runs the bash of its agent in the sandbox of the
# Claude Agent SDK, which needs them on Linux (spec 003, V14).
#
# git: briar reads owner/repo from the mounted repo's `origin`. gh: the
# pipelines of briar 2.0 (spec 003) clone, push and open PRs through it.
# GitHub logins do not use gh (spec 005, G10): briar reads
# BRIAR_<COMPANY>_GITHUB_TOKEN from the environment or secrets.env.
#
# safe.directory '*': the repo you mount at /work belongs to your host user,
# which on Linux is rarely the image's uid 1000, and git then refuses it
# ("dubious ownership"), so briar cannot read owner/repo from `origin`. That
# check guards shared multi-user machines; here the only user is briar and
# the folder is one you mounted on purpose.
#
# gh at the Homebrew paths: `gh auth setup-git` on a Mac writes the absolute
# path of gh into ~/.gitconfig. Mounted into the container, that line replaces
# the helper above and points at a path the image lacks, so the push fails
# with "gh: not found". The links make that helper line work here too.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git gh bubblewrap socat \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system credential.https://github.com.helper '!gh auth git-credential' \
    && git config --system --add safe.directory '*' \
    && mkdir -p /opt/homebrew/bin \
    && ln -s /usr/bin/gh /usr/local/bin/gh \
    && ln -s /usr/bin/gh /opt/homebrew/bin/gh

# Drop privileges — the CLI never needs root.
RUN useradd --create-home --uid 1000 briar
USER briar
WORKDIR /home/briar

ENTRYPOINT ["briar"]
CMD ["--help"]
