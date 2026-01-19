#!/bin/bash -eu

apt-get update

export PATH="/usr/bin:$PATH"

export SANITIZER=${SANITIZER:-address}
export FUZZING_ENGINE=${FUZZING_ENGINE:-libfuzzer}
export FUZZING_LANGUAGE=${FUZZING_LANGUAGE:-c++}
export ARCHITECTURE=${ARCHITECTURE:-x86_64}
export CXXFLAGS="$CXXFLAGS -g3 -O0"
export CFLAGS="$CFLAGS -g3 -O0"

echo "[*] backup old files"
mv $OUT $OUT.bak && mkdir $OUT
# mv $WORK $WORK.bak && mkdir $WORK
echo "[*] Clean up old builds"
rm -rf $WORK && mkdir -p $WORK


compile
