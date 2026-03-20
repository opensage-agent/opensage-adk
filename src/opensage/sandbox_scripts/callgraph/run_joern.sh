#!/bin/bash

set -e

CODE_DIR="$1"
# OUT_PATH="/shared/out/callgraph/joern_export.xml"
# OUT_DIR="/shared/out/callgraph"
OUT_DIR="/shared/neo4j/import"

mkdir -p $OUT_DIR

joern-parse "$CODE_DIR" -o cpg.bin

# Move cpg.bin to /cpg.bin so it's accessible from root
if [ -f cpg.bin ]; then
    mv cpg.bin /cpg.bin
    echo "CPG file created at /cpg.bin"
else
    echo "Error: cpg.bin was not created" >&2
    exit 1
fi

joern --script /sandbox_scripts/callgraph/extract_call.scala --param cpgFile=/cpg.bin --param outDir=$OUT_DIR

# joern-export cpg.bin --out=graphml --repr=all --format=graphml
# cp graphml/export.xml $OUT_PATH
