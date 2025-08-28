#!/bin/bash

# Source this script in the calling shell:
#  $ . ./env.sh
# to set, clear or show the calling shell's environment variables for Circuit CLI.

if [ "$1" = "-h" ]; then
  echo "Usage: . ./env.sh [OPTIONS] COMMAND [ARG]"
  echo ""
  echo "  Manage environment variables for Circuit keeper bots"
  echo ""
  echo "Options:"
  echo "  -h, --help                  Show this message and exit"
  echo ""
  echo "Commands:"
  echo "  clear     Clear environment variables"
  echo "  set       Set environment variables for specified environment"
  echo "  show      Show environment variables currently set"
else
  if [ "$1" = "set" ]; then
    if [ "$2" = "-h" ] || [ "$2" = "--help" ]; then
      echo "Usage: . ./env.sh set [ARGS]"
      echo ""
      echo "  Set environment variables for Circuit keeper bots"
      echo ""
      echo "  PRIVATE_KEY environment variable must be set manually"
      echo "  or passed as cmd line argument to CLI with -p option."
      echo ""
      echo "Arguments:"
      echo "  -h, --help     Show this message and exit"
      echo "  main           Set environment variables for mainnet"
      echo "  test           Set environment variables for testnet"
      echo "  sim            Set environment variables for simulator"
      return
    elif [ "$2" = "main" ]; then
        export RPC_URL="https://api.circuitdao.com"
        export ADD_SIG_DATA="ccd5bb71183532bff220ba46c268991a3ff07eb358e8255a65c30a2dce0e5fbb" # needed? genesis_challege
        export FEE_PER_COST=5
    elif [ "$2" = "test" ]; then
        export RPC_URL="https://testnet-api.circuitdao.com"
        export ADD_SIG_DATA="37a90eb5185a9c4439a91ddc98bbadce7b4feba060d50116a067de66bf236615" # testnet11
        export FEE_PER_COST=7
    elif [ "$2" = "sim" ]; then
        export RPC_URL="http://localhost:8000"
        export ADD_SIG_DATA="" # leave empty
        export FEE_PER_COST=7
    else
      echo "Unkown environment $2. Use -h for help"
      return
    fi
  elif [ "$1" = "clear" ]; then
    export PRIVATE_KEY=""
    export RPC_URL=""
    export ADD_SIG_DATA=""
    export FEE_PER_COST=""
  elif [ "$1" != "show" ]; then
    echo "Unkown argument $1. Use -h for help"
    return
  fi
  if [ -z "${PRIVATE_KEY}" ]; then
      echo "PRIVATE_KEY:          "
  elif [ "$2" = "--show-private-key" ]; then
      echo "PRIVATE_KEY:          $PRIVATE_KEY"
  else
      echo "PRIVATE_KEY:          ****  (use --show-private-key to show)"
  fi
  echo "RPC_URL:              $RPC_URL"
  echo "ADD_SIG_DATA:         $ADD_SIG_DATA"
  echo "FEE_PER_COST:         $FEE_PER_COST"
fi
