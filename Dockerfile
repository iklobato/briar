# briar-cli container image.
#
# Built in CI from the wheel produced by `uv build` (see .github/workflows/
# release.yml), so the image always matches the version published to PyPI.
#
#   docker run --rm iklob1/briar version
#   docker run --rm -p 8080:8080 iklob1/briar dashboard --host 0.0.0.0
FROM python:3.11-slim

# Ownership proof for the official MCP registry; must match `name` in its server.json.
LABEL io.modelcontextprotocol.server.name="io.github.iklobato/briar"

# Install the wheel CI just built, WITH the `mcp` extra so `briar mcp serve`
# (FastMCP) works in the image — without it the server crashes on
# `ModuleNotFoundError: No module named 'mcp'`. The glob resolves to the
# single versioned wheel in dist/; `[mcp]` pulls the extra from PyPI.
COPY dist/ /tmp/dist/
RUN whl="$(ls /tmp/dist/*.whl)" && pip install --no-cache-dir "${whl}[mcp]" && rm -rf /tmp/dist

# `briar agent` and `plan run` clone, commit and push with git and open or
# read PRs with gh; without them every agent run fails inside the image.
# After cloning, briar strips the token from `origin`, so pushes to GitHub
# authenticate through gh, which reads GITHUB_TOKEN from the environment.
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
    && apt-get install -y --no-install-recommends git gh \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system credential.https://github.com.helper '!gh auth git-credential' \
    && git config --system --add safe.directory '*' \
    && mkdir -p /opt/homebrew/bin \
    && ln -s /usr/bin/gh /usr/local/bin/gh \
    && ln -s /usr/bin/gh /opt/homebrew/bin/gh

# The built-in think/time MCP servers need npx and uvx, which the image does
# not ship; skip them instead of warning on every run. `-e
# BRIAR_NO_DEFAULT_MCP=0` turns them back on for a derived image that adds them.
ENV BRIAR_NO_DEFAULT_MCP=1

# Drop privileges — the CLI never needs root.
RUN useradd --create-home --uid 1000 briar
USER briar
WORKDIR /home/briar

ENTRYPOINT ["briar"]
CMD ["--help"]
