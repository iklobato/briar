# briar-cli container image.
#
# Built in CI from the wheel produced by `uv build` (see .github/workflows/
# release.yml), so the image always matches the version published to PyPI.
# The `[mcp]` extra is installed so `briar mcp serve` and `briar chat` work
# out of the box.
#
#   docker run --rm iklobato/briar version
#   docker run --rm -i iklobato/briar mcp serve --transport stdio
FROM python:3.11-slim

# Install the wheel CI just built (with the mcp extra). The glob resolves to
# the single versioned wheel in dist/; the [mcp] suffix pulls the MCP SDK.
COPY dist/ /tmp/dist/
RUN pip install --no-cache-dir "$(ls /tmp/dist/*.whl)[mcp]" && rm -rf /tmp/dist

# Drop privileges — the CLI never needs root.
RUN useradd --create-home --uid 1000 briar
USER briar
WORKDIR /home/briar

ENTRYPOINT ["briar"]
CMD ["--help"]
