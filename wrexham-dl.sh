#!/bin/bash

set -e

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
script_path=${script_dir}/$(basename "${BASH_SOURCE[0]}")
script_name=$(basename "${BASH_SOURCE[0]}")

cd "${script_dir}"

media_id= # defaults to live
media_type= # defaults to video

while test $# -gt 0; do
  case "$1" in
    --id) media_id="$2"; shift;;
    --video) media_type="video";;
    --audio) media_type="audio";;
    *) >&2 echo "Bad argument $1"; exit 1;;
  esac
  shift
done

if [ ! -d .venv ]; then
  python3 -Im venv .venv --clear
  .venv/bin/pip3 install --upgrade pip
  .venv/bin/pip3 install --upgrade setuptools
  .venv/bin/pip3 install -r requirements.txt
fi

source .venv/bin/activate

if [ -n "${media_id}" ]; then
  media_id="--id '${media_id}'"
fi

if [ -n "${media_type}" ]; then
  media_type="--type '${media_type}'"
fi

python3 wrexham-dl.py ${media_id} ${media_type}