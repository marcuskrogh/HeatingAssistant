#!/bin/bash
# Build musllinux cyipopt wheels (Ipopt + MUMPS + OpenBLAS bundled) for HAOS.
# Intended to run inside quay.io/pypa/musllinux_1_2_{x86_64,aarch64}.
set -euxo pipefail

TAG="${1:-v1.7.0}"
PY_TAGS="${PY_TAGS:-cp312-cp312 cp313-cp313}"
OUT_DIR="${OUT_DIR:-/wheels}"

apk add --no-cache \
  build-base \
  cmake \
  gfortran \
  git \
  linux-headers \
  openblas-dev \
  pkgconfig \
  wget

export LD_LIBRARY_PATH="/usr/local/lib:/usr/lib:${LD_LIBRARY_PATH:-}"

WORKDIR=/tmp/cyipopt-musl-build
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR" "$OUT_DIR"
cd "$WORKDIR"

git clone https://github.com/coin-or-tools/ThirdParty-Mumps --depth=1 --branch releases/3.0.4
pushd ThirdParty-Mumps
./get.Mumps
./configure --with-lapack="-lopenblas"
make -j"$(nproc)"
make install
popd

git clone https://github.com/coin-or/Ipopt --depth=1 --branch releases/3.14.11
pushd Ipopt
./configure --with-lapack="-lopenblas"
make -j"$(nproc)"
make install
popd

git clone https://github.com/mechmotum/cyipopt --depth=1 --branch "$TAG"
pushd cyipopt
mkdir -p dist wheelhouse
for PYVERSION in $PY_TAGS; do
  PYBIN="/opt/python/${PYVERSION}/bin"
  if [[ ! -x "${PYBIN}/python" ]]; then
    echo "Skipping missing interpreter ${PYVERSION}"
    continue
  fi
  "${PYBIN}/pip" install --upgrade pip setuptools wheel cython "numpy>=1.26.4"
  "${PYBIN}/pip" wheel --no-deps --wheel-dir=./dist .
done

for wheel in dist/cyipopt-*.whl; do
  auditwheel repair "$wheel" -w wheelhouse
done

cp -a wheelhouse/cyipopt-*.whl "$OUT_DIR"/
popd

echo "Built wheels:"
ls -lh "$OUT_DIR"
