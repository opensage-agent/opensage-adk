#!/bin/bash

set -e  # Exit on any error

# Check if required arguments are provided
if [ $# -ne 1 ]; then
    echo "Usage: $0 <build_command>"
    echo "Example: $0 'make clean && make'"
    exit 1
fi

WORK_DIR="/work"
BUILD_COMMAND="$1"
CODEQL_BIN="/sandbox_scripts/codeql/codeql"
DATABASE_PATH="$WORK_DIR/.opensage-codeql-database"
QUERY_DIR="/sandbox_scripts/callgraph/codeql_queries"
OUT_DIR="/shared/out/callgraph"

mkdir -p $WORK_DIR
mkdir -p $OUT_DIR

cp -r $QUERY_DIR $WORK_DIR/.codeql_queries
QUERY_DIR="$WORK_DIR/.codeql_queries"



echo "Starting CodeQL analysis with build command: $BUILD_COMMAND"

$CODEQL_BIN pack install $QUERY_DIR/qlpack.yml

# Step 1: Build CodeQL database
echo "Creating CodeQL database..."
$CODEQL_BIN database create $DATABASE_PATH \
    --language=cpp \
    --overwrite \
    --threads=$(nproc) \
    --command="$BUILD_COMMAND"

# Step 2: Run direct calls query
echo "Running direct calls query..."
$CODEQL_BIN query run \
    --database=$DATABASE_PATH \
    --output=/work/direct_callgraph.bqrs \
    $QUERY_DIR/directCalls.ql

# Step 3: Decode direct call graph results
echo "Decoding direct call graph results..."
$CODEQL_BIN bqrs decode /work/direct_callgraph.bqrs \
    --format=csv \
    --output=/work/results.csv

# Step 4: Find function pointer accesses
echo "Finding function pointer accesses..."
$CODEQL_BIN query run \
    --database=$DATABASE_PATH \
    --output=/work/fp_accesses.bqrs \
    $QUERY_DIR/funcPtrAccesses.ql

# Step 5: Decode function pointer accesses
echo "Decoding function pointer accesses..."
$CODEQL_BIN bqrs decode /work/fp_accesses.bqrs \
    --format=csv \
    --output=/work/fp_accesses.csv

# Step 6: Find expression calls
echo "Finding expression calls..."
$CODEQL_BIN query run \
    --database=$DATABASE_PATH \
    --output=/work/expr_calls.bqrs \
    $QUERY_DIR/exprCalls.ql

# Step 7: Decode expression calls
echo "Decoding expression calls..."
$CODEQL_BIN bqrs decode /work/expr_calls.bqrs \
    --format=csv \
    --output=/work/expr_calls.csv

cp /work/results.csv /work/fp_accesses.csv /work/expr_calls.csv $OUT_DIR/

echo "CodeQL analysis completed successfully!"
echo "Output files:"
echo "  - Direct calls: $OUT_DIR/results.csv"
echo "  - Function pointer accesses: $OUT_DIR/fp_accesses.csv"
echo "  - Expression calls: $OUT_DIR/expr_calls.csv"
