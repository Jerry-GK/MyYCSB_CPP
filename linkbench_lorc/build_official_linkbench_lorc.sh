#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LINKBENCH_HOME="${LINKBENCH_HOME:-/home/gjr/projects/linkbench_official}"
LORC_HOME="${LORC_HOME:-/home/gjr/mylibs/lorcdb_release}"
LSBM_HOME="${LSBM_HOME:-/home/gjr/projects/lsbm}"
LSBM_BUILD_DIR="${LSBM_BUILD_DIR:-/home/gjr/projects/lsbm_pic_build}"
JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-11-openjdk-amd64}"
if [[ ! -x "${JAVA_HOME}/bin/javac" ]]; then
  JAVA_HOME="/usr/lib/jvm/java-11-openjdk-amd64"
fi
BUILD_DIR="${SCRIPT_DIR}/build"

mkdir -p "${BUILD_DIR}"

install -D \
  "${SCRIPT_DIR}/java/com/facebook/LinkBench/LinkStoreLorcKV.java" \
  "${LINKBENCH_HOME}/src/main/java/com/facebook/LinkBench/LinkStoreLorcKV.java"

(
  cd "${LINKBENCH_HOME}"
  JAVA_HOME="${JAVA_HOME}" PATH="${JAVA_HOME}/bin:${PATH}" \
    mvn -DskipTests package dependency:copy-dependencies
)

if [[ ! -f "${LSBM_BUILD_DIR}/lsbm/libdb_lsmcb.a" ]]; then
  cmake -S "${LSBM_HOME}" -B "${LSBM_BUILD_DIR}" \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_CXX_FLAGS="-fPIC"
  cmake --build "${LSBM_BUILD_DIR}" -- -j"$(nproc)"
fi

g++ -O3 -DNDEBUG -DLEVELDB_PLATFORM_POSIX -std=c++17 -fPIC -shared \
  "${SCRIPT_DIR}/native/lorc_linkbench_jni.cc" \
  -o "${BUILD_DIR}/liblorc_linkbench_jni.so" \
  -I"${JAVA_HOME}/include" -I"${JAVA_HOME}/include/linux" \
  -I"${LORC_HOME}/include" -I"${LSBM_HOME}/include" -I"${LSBM_HOME}" \
  -L"${LORC_HOME}/lib" -Wl,-rpath,"${LORC_HOME}/lib" \
  -lrocksdb \
  -Wl,--start-group \
  "${LSBM_BUILD_DIR}/lsbm/libdb_lsmcb.a" \
  "${LSBM_BUILD_DIR}/common/libdb_common.a" \
  "${LSBM_BUILD_DIR}/table/libtable.a" \
  "${LSBM_BUILD_DIR}/util/libutil.a" \
  "${LSBM_BUILD_DIR}/port/libport.a" \
  -Wl,--end-group \
  -ldl -lpthread -lsnappy -lz -llz4

echo "Built ${BUILD_DIR}/liblorc_linkbench_jni.so"
