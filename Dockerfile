# briar-cli container image.
#
# Built in CI from the wheel produced by `uv build` (see .github/workflows/
# release.yml), so the image always matches the version published to PyPI.
#
#   docker run --rm iklob1/briar version
#   docker run --rm -p 8080:8080 iklob1/briar dashboard --host 0.0.0.0
FROM python:3.11-slim

# Install the wheel CI just built, WITH the `mcp` extra so `briar mcp serve`
# (FastMCP) works in the image — without it the server crashes on
# `ModuleNotFoundError: No module named 'mcp'`. The glob resolves to the
# single versioned wheel in dist/; `[mcp]` pulls the extra from PyPI.
COPY dist/ /tmp/dist/
RUN whl="$(ls /tmp/dist/*.whl)" && pip install --no-cache-dir "${whl}[mcp]" && rm -rf /tmp/dist

# Drop privileges — the CLI never needs root.
RUN useradd --create-home --uid 1000 briar
USER briar
WORKDIR /home/briar

ENTRYPOINT ["briar"]
CMD ["--help"]
