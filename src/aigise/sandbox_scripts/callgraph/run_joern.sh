#!/bin/bash

set -e

CODE_DIR="/shared/code"
# OUT_PATH="/shared/out/callgraph/joern_export.xml"
# OUT_DIR="/shared/out/callgraph"
OUT_DIR="/shared/neo4j/import"

mkdir -p $OUT_DIR

joern-parse $CODE_DIR -o cpg.bin

joern --script /sandbox_scripts/callgraph/extract_call.scala --param cpgFile=cpg.bin --param outDir=$OUT_DIR

# joern-export cpg.bin --out=graphml --repr=all --format=graphml
# cp graphml/export.xml $OUT_PATH
