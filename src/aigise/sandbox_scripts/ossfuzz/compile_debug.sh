#!/bin/bash -eu

export SANITIZER=${SANITIZER:-address}
export FUZZING_ENGINE=${FUZZING_ENGINE:-libfuzzer}
export FUZZING_LANGUAGE=${FUZZING_LANGUAGE:-c++}
export ARCHITECTURE=${ARCHITECTURE:-x86_64}
export CXXFLAGS="$CXXFLAGS -g3 -O0"
export CFLAGS="$CFLAGS -g3 -O0"

# echo "[*] backup old files"
# mv $OUT $OUT.bak && mkdir $OUT
# mv $WORK $WORK.bak && mkdir $WORK

compile
