FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN dpkg --add-architecture arm64 \
    && sed -i 's/^deb /deb [arch=amd64] /' /etc/apt/sources.list \
    && printf '%s\n' \
       'deb [arch=arm64] http://ports.ubuntu.com/ubuntu-ports jammy main universe' \
       'deb [arch=arm64] http://ports.ubuntu.com/ubuntu-ports jammy-updates main universe' \
       'deb [arch=arm64] http://ports.ubuntu.com/ubuntu-ports jammy-security main universe' \
       > /etc/apt/sources.list.d/arm64.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       gcc-aarch64-linux-gnu g++-aarch64-linux-gnu make cmake meson ninja-build \
       pkg-config python3 file git autoconf automake libtool \
       libsdl2-dev:arm64 libsdl2-image-dev:arm64 libsdl2-mixer-dev:arm64 \
       libsdl2-ttf-dev:arm64 libfreetype6-dev:arm64 libpng-dev:arm64 \
       libjpeg-dev:arm64 libopenal-dev:arm64 libvorbis-dev:arm64 libogg-dev:arm64 \
       libcurl4-openssl-dev:arm64 zlib1g-dev:arm64 libgl-dev:arm64 libegl-dev:arm64
COPY aarch64.cmake aarch64.ini build.py /opt/cross/
ENV CC=aarch64-linux-gnu-gcc CXX=aarch64-linux-gnu-g++ AR=aarch64-linux-gnu-ar
ENV PKG_CONFIG_LIBDIR=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig
ENV CFLAGS="-O2 -mcpu=cortex-a53 -mtune=cortex-a53 -fsigned-char"
ENV CXXFLAGS="-O2 -mcpu=cortex-a53 -mtune=cortex-a53 -fsigned-char"
ENTRYPOINT ["python3", "/opt/cross/build.py"]
