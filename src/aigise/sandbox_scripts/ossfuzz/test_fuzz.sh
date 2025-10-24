#!/bin/bash -eu

SCRIPT_DIR=$(dirname "$(readlink -f "$0")")

source "${SCRIPT_DIR}/env_run_afl"

mkdir /fuzz && cd /fuzz && mkdir in out

echo 1234 > in/seed

export AFL_NO_UI=1

timeout 30s /out/afl-fuzz -i in -o out /out/$FUZZ_TARGET > /tmp/fuzz.log

KEYWORD="We're done here. Have a nice day!"

if grep -q "$KEYWORD" /tmp/fuzz.log; then
    echo "[+] Fuzzing completed successfully."
else
    echo "[-] Fuzzing did not complete as expected."
    echo "[*] Fuzzing log:"
    cat /tmp/fuzz.log
    exit 1
fi
