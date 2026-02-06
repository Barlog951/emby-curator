# -- Build stage -- #
FROM python:3.14-slim AS build-stage

# Set build-time metadata as defined at http://label-schema.org
ARG BUILD_DATE
ARG VCS_REF
ARG VCS_URL="https://github.com/troykelly/emby-dedupe"
ARG VERSION="edge"

LABEL org.label-schema.build-date=$BUILD_DATE \
    org.label-schema.name="emby-dedupe" \
    org.label-schema.description="A Docker container to run the Emby deduplication script." \
    org.label-schema.url="https://github.com/troykelly/emby-dedupe#readme" \
    org.label-schema.vcs-ref=$VCS_REF \
    org.label-schema.vcs-url=$VCS_URL \
    org.label-schema.vendor="Troy Kelly" \
    org.label-schema.version=$VERSION \
    org.label-schema.schema-version="1.0" \
    org.opencontainers.image.source=$VCS_URL

# Set the working directory
WORKDIR /build

# Create a virtual environment to isolate our package dependencies locally
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy source code and setup file
COPY emby_dedupe/ ./emby_dedupe/
COPY setup.py .
COPY requirements.txt .

# Install the package and its dependencies
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir .

# -- Final stage -- #
FROM python:3.14-slim

WORKDIR /app

# Import the virtual environment from the build stage
COPY --from=build-stage /opt/venv /opt/venv

# Make sure scripts in the virtualenv are usable
ENV PATH="/opt/venv/bin:$PATH"

# Copy the built application from the build stage to the final stage
COPY --from=build-stage /build/emby_dedupe /app/emby_dedupe
COPY --from=build-stage /build/setup.py /app/

# Copy everything from rootfs to the root of the container
COPY rootfs/ /

# Set correct permissions for the entrypoint script
RUN chmod +x /usr/local/sbin/entrypoint

# We run our application as a non-root user for security reasons.
RUN useradd --create-home --shell /bin/bash embyuser
USER embyuser

# Healthcheck to verify the application is installed correctly
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import emby_dedupe; print('OK')" || exit 1

# Set the entrypoint script as the Docker entrypoint
ENTRYPOINT ["/usr/local/sbin/entrypoint"]