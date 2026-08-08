ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.20
# Supervisor passes BUILD_VERSION from config.yaml on every App build/update.
# Keep the default equal to heating_assistant/config.yaml version for local builds.
ARG BUILD_VERSION=2.0.4
ARG BUILD_ARCH=amd64
FROM ${BUILD_FROM}

# Re-declare so the values are available after FROM (Docker ARG scope).
ARG BUILD_VERSION
ARG BUILD_ARCH
# Changing BUILD_VERSION invalidates this layer and all following COPY/RUN layers,
# which avoids stale App code from sticky containerd caches.
RUN mkdir -p /usr/share/heatingassistant \
    && printf '%s\n' "${BUILD_VERSION}" > /usr/share/heatingassistant/APP_VERSION \
    && echo "Building HeatingAssistant App ${BUILD_VERSION} (${BUILD_ARCH})"

LABEL io.hass.version="${BUILD_VERSION}" \
      io.hass.type="app" \
      io.hass.arch="${BUILD_ARCH}" \
      io.hass.name="HeatingAssistant"

# Install from the App build context (synced package + pyproject.toml).
# Avoids needing git / a second GitHub clone during docker build.
WORKDIR /usr/src/heatingassistant
COPY pyproject.toml README.md ./
COPY heatingassistant ./heatingassistant
# Runtime deps come from pyproject (numpy/scipy/highspy/mbc + paho-mqtt).
RUN pip3 install --no-cache-dir .

# Thin integration is auto-copied into HA /config on App start.
COPY custom_components /usr/share/heatingassistant/custom_components

COPY run.sh /
RUN chmod a+x /run.sh

WORKDIR /
CMD ["/run.sh"]
