#!/bin/bash

set -e

CODE_DIR="/shared/code"
OUT_PATH="/shared/out/callgraph/joern_export.xml"

joern-parse $CODE_DIR -o cpg.bin
joern-export cpg.bin --out=graphml --repr=all --format=graphml

cp graphml/export.xml $OUT_PATH
