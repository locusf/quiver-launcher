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
       gcc-aarch64-linux-gnu g++-aarch64-linux-gnu ccache make cmake meson ninja-build \
       pkg-config python3 file git autoconf automake libtool \
       libsdl2-dev:arm64 libsdl2-image-dev:arm64 libsdl2-mixer-dev:arm64 \
       libsdl2-ttf-dev:arm64 libfreetype6-dev:arm64 libpng-dev:arm64 \
       libjpeg-dev:arm64 libopenal-dev:arm64 libvorbis-dev:arm64 libogg-dev:arm64 \
       libboost-locale-dev:arm64 libboost-program-options-dev:arm64 libboost-date-time-dev:arm64 \
       libcurl4-openssl-dev:arm64 zlib1g-dev:arm64 libgl-dev:arm64 libegl-dev:arm64
RUN mkdir -p /opt/ccache-bin \
    && ln -s /usr/bin/ccache /opt/ccache-bin/aarch64-linux-gnu-gcc \
    && ln -s /usr/bin/ccache /opt/ccache-bin/aarch64-linux-gnu-g++
COPY aarch64.cmake aarch64.ini build.py /opt/cross/
COPY toolchain-check /opt/cross/toolchain-check
RUN cmake -S /opt/cross/toolchain-check -B /tmp/toolchain-check \
        -DCMAKE_TOOLCHAIN_FILE=/opt/cross/aarch64.cmake -DBoost_NO_BOOST_CMAKE=ON \
    && cmake --build /tmp/toolchain-check \
    && aarch64-linux-gnu-readelf -h /tmp/toolchain-check/check-toolchain | grep 'Machine:.*AArch64'
ENV CC=aarch64-linux-gnu-gcc CXX=aarch64-linux-gnu-g++ AR=aarch64-linux-gnu-ar
ENV PKG_CONFIG_LIBDIR=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig
ENV CFLAGS="-O2 -mcpu=cortex-a53 -mtune=cortex-a53 -fsigned-char"
ENV CXXFLAGS="-O2 -mcpu=cortex-a53 -mtune=cortex-a53 -fsigned-char"
ENV PATH="/opt/ccache-bin:${PATH}"
ENV CCACHE_DIR=/ccache CCACHE_BASEDIR=/tmp/game CCACHE_MAXSIZE=1G CCACHE_COMPILERCHECK=content
ENTRYPOINT ["python3", "/opt/cross/build.py"]
