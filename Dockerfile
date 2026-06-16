# briar-cli container image.
#
# Built in CI from the wheel produced by `uv build` (see .github/workflows/
# release.yml), so the image always matches the version published to PyPI.
#
#   docker run --rm iklobato/briar version
#   docker run --rm -p 8080:8080 iklobato/briar dashboard --host 0.0.0.0
FROM python:3.11-slim

# Install the wheel CI just built. The glob resolves to the single versioned
# wheel in dist/.
COPY dist/ /tmp/dist/
RUN pip install --no-cache-dir /tmp/dist/*.whl && rm -rf /tmp/dist

# Drop privileges — the CLI never needs root.
RUN useradd --create-home --uid 1000 briar
USER briar
WORKDIR /home/briar

ENTRYPOINT ["briar"]
CMD ["--help"]
