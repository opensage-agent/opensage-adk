#!/bin/bash

BINARY=$1
PROFDATA=$2
NAME_REGEX=$3

VERSION=$(llvm-cov --version | grep 'LLVM version' | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')

if dpkg --compare-versions "$VERSION" "ge" "15.0.0"; then
    llvm-cov show \
        -object="$BINARY" \
        -instr-profile="$PROFDATA" \
        -show-line-counts-or-regions \
        -show-branches=count \
        -name-regex="$NAME_REGEX"
else
    llvm-cov show \
        -object="$BINARY" \
        -instr-profile="$PROFDATA" \
        -show-line-counts-or-regions \
        -name-regex="$NAME_REGEX"
fi
