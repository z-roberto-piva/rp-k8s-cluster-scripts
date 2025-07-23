#!/bin/bash

echo "# Auto-generated from .env" > secrets.auto.tfvars

while IFS='=' read -r key value; do
  [[ $key == \#* || -z $key ]] && continue
  echo "$key = \"${value}\"" >> secrets.auto.tfvars
done < ../.env
