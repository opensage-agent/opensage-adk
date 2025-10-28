#!/bin/bash

PROFDATA=$1
NAME_REGEX=$2

llvm-cov show \
    -instr-profile="$PROFDATA" \
    -object=/out/magic_fuzzer \
    -show-line-counts-or-regions \
    -show-branches=count \
    -name-regex="$NAME_REGEX"
