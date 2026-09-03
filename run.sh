#!/bin/sh
# Cron does not inherit the container env; materialize it for each job.
printenv | awk -F= '
  NF && $1 !~ /^(PWD|SHLVL|_|HOME|HOSTNAME|TERM)$/ {
    key=$1
    val=substr($0, index($0, "=") + 1)
    gsub(/\\/, "\\\\", val)
    gsub(/"/, "\\\"", val)
    printf "export %s=\"%s\"\n", key, val
  }
' > /app/cron-env.sh

exec "$@"
