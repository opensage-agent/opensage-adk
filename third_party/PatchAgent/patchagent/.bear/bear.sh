#!/bin/bash

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cp $DIR/bin/bear /usr/local/bin/
cp -r $DIR/lib/bear /usr/local/lib/

echo $(realpath $DIR/..) > $OUT/.pwd
bear --output $OUT/compile_commands.json -- compile
