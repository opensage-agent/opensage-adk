#!/bin/bash -eu

apt-get update

export PATH="/usr/bin:$PATH"

export FUZZING_ENGINE=afl
export FUZZING_LANGUAGE=${FUZZING_LANGUAGE:-c++}
export ARCHITECTURE=${ARCHITECTURE:-x86_64}
export LIB_FUZZING_ENGINE_DEPRECATED=${LIB_FUZZING_ENGINE_DEPRECATED:-/usr/lib/libFuzzingEngine.a}

echo "[*] backup old files"
mv $OUT $OUT.bak && mkdir $OUT
# mv $WORK $WORK.bak && mkdir $WORK
echo "[*] Clean up old builds"
rm -rf $WORK && mkdir -p $WORK


SCRIPT_DIR=$(dirname "$(readlink -f "$0")")
pushd $SCRIPT_DIR > /dev/null

chmod +x download_afl precompile_afl compile_afl compile

echo "[*] Download AFL++"
./download_afl

echo "[*] Build AFL++"
source ./fix_clang
source ./fix_libstd
./precompile_afl
mkdir -p /usr/local/lib/x86_64-unknown-linux-gnu/

echo "[*] Move compile_afl to /usr/local/bin/compile_afl"
# default to /usr/local/bin/compile_afl if not exists in PATH
COMPILE_AFL_PATH=$(command -v compile_afl || echo /usr/local/bin/compile_afl)

if [ -f "$COMPILE_AFL_PATH" ]; then
    cp "$COMPILE_AFL_PATH" "$COMPILE_AFL_PATH.bak"
fi

cp compile_afl $COMPILE_AFL_PATH
popd > /dev/null

echo "[*] Compile the project with AFL++"

echo "[*] Fixing different project settings..."
source $SCRIPT_DIR/fix_project

$SCRIPT_DIR/compile
