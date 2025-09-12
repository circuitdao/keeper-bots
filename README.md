# Keeper Bots

Keeper bots for Circuit protocol that automate the execution of keeper operations.

## Overview

The keeper bots system consists of specialized bots that monitor and maintain various aspects of the Circuit protocol. Each bot runs independently and can be configured for different network environments.

## Environment Configuration

### Using env.sh Script

The `env.sh` script provides a convenient way to manage environment variables for the keeper bots. It supports three different environments:

#### Usage

Source the script in your shell:
```bash
# Show help
. ./env.sh -h

# Set environment for mainnet
. ./env.sh set main

# Set environment for testnet
. ./env.sh set test

# Set environment for simulator
. ./env.sh set sim

# Show current environment variables
. ./env.sh show

# Clear environment variables
. ./env.sh clear
```

#### Environment Variables

The following environment variables are managed by the env.sh script:

| Variable | Description | Required |
|----------|-------------|----------|
| `PRIVATE_KEY` | Private key for signing transactions | Yes* |
| `RPC_URL` | Circuit API endpoint URL | Yes |
| `ADD_SIG_DATA` | Additional signature data (genesis challenge) | Yes |
| `FEE_PER_COST` | Fee per cost unit for transactions | Yes |

*Note: `PRIVATE_KEY` must be set manually or passed as a command line argument to CLI with `-p` option.

### Generating Chia Private Keys

**⚠️ Security Warning**: Private keys control access to your funds. Always generate keys securely and never share them.

#### Method 1: Using Chia CLI (Recommended)

The most secure way to generate a Chia private key is using the official Chia CLI:

```bash
# Install Chia if not already installed
pip install chia-blockchain

# Generate a new wallet and get the private key
chia keys generate

# Show existing keys (lists all wallets)
chia keys show

# Get private key for a specific wallet (replace with your fingerprint)
chia keys show --show-private-keys --fingerprint YOUR_FINGERPRINT
```

The private key will be displayed in the format required by the keeper bots.

#### Method 2: Generate for Testing/Development

For development and testing purposes, you can create a simple test private key:

```bash
# Generate a random 32-byte private key for testing only
python3 -c "import secrets; print(secrets.token_hex(32))"
```

**⚠️ Warning**: This method is only suitable for testing and development. For production use, always use Method 1 (Chia CLI) to ensure proper key derivation and security.

#### Method 3: Import Existing Wallet

If you have an existing Chia wallet mnemonic:

```bash
# Import existing wallet from mnemonic
chia keys add

# Then show the private key
chia keys show --show-private-keys
```

#### Security Best Practices

- **Never store private keys in plain text files**
- **Use environment variables or secure key management systems**
- **For production, consider hardware wallets or HSMs**
- **Test with small amounts first on testnet**
- **Keep backup of your mnemonic phrase in a secure location**
- **Use different keys for different environments (mainnet/testnet)**

#### Testing Your Private Key

Before using a private key with keeper bots, verify it works:

```bash
# Set your private key
export PRIVATE_KEY="your_generated_private_key_here"

# Configure for testnet
. ./env.sh set test

# Test with a simple bot operation
poetry run python -m keeper_bots.announcer_configure_bot --dry-run
```

#### Network Configurations

**Mainnet (`main`)**:
- `RPC_URL`: https://api.circuitdao.com
- `ADD_SIG_DATA`: ccd5bb71183532bff220ba46c268991a3ff07eb358e8255a65c30a2dce0e5fbb
- `FEE_PER_COST`: 5

**Testnet (`test`)**:
- `RPC_URL`: https://testnet-api.circuitdao.com
- `ADD_SIG_DATA`: 37a90eb5185a9c4439a91ddc98bbadce7b4feba060d50116a067de66bf236615
- `FEE_PER_COST`: 7

**Simulator (`sim`)**:
- `RPC_URL`: http://localhost:8000
- `ADD_SIG_DATA`: (empty)
- `FEE_PER_COST`: 7

## Available Bots

### Oracle Complex

**announcer_configure_bot**
- Configures price announcers
- Usage: `python -m keeper_bots.announcer_configure_bot`

**announcer_update_bot**
- Updates announcer price from external sources (OKX)
- Usage: `python -m keeper_bots.announcer_update_bot`

**oracle_update_bot**
- Adds new prices to oracle price queue
- Usage: `python -m keeper_bots.oracle_update_bot`

**statutes_update_bot**
- Updates statutes price
- Usage: `python -m keeper_bots.statutes_update_bot`

### Collateral Vaults

**liquidation_start_bot**
- Triggers liquidation auctions for undercollateralized positions
- Usage: `python -m keeper_bots.liquidation_start_bot`

**liquidation_bid_bot**
- Places bids in liquidation auctions and automatically hedges exposure (on OKX)
- Usage: `python -m keeper_bots.liquidation_bid_bot`

**bad_debt_recovery_bot**
- Recovers bad debt from collateral vaults
- Usage: `python -m keeper_bots.bad_debt_recovery_bot`

### Auction Bots

**recharge_start_settle_bot**
- Starts and settles recharge auctions
- Usage: `python -m keeper_bots.recharge_start_settle_bot`

**surplus_start_settle_bot**
- Starts and settles surplus auctions
- Usage: `python -m keeper_bots.surplus_start_settle_bot`

### Treasury and Savings

**treasury_rebalance_bot**
- Rebalances the treasury
- Usage: `python -m keeper_bots.treasury_rebalance_bot`

**savings_bot**
- Withdraws accrued interest from treasury to savings vault according to a customizable schedule
- Usage: `python -m keeper_bots.savings_bot`

### Governance

**governance_bot**
- Handles governance-related operations
- Usage: `python -m keeper_bots.governance_bot`

### Supporting Utilities

**recharge_bot**
- General recharge operations
- Usage: `python -m keeper_bots.recharge_bot`

**stability_fee_transfer_bot**
- Transfers stability fees
- Usage: `python -m keeper_bots.stability_fee_transfer_bot`

## Running Bots

### Prerequisites

1. **Install dependencies**:
   ```bash
   poetry install
   ```

2. **Set up environment**:
   ```bash
   # Set your private key
   export PRIVATE_KEY="your_private_key_here"
   
   # Configure environment (choose one)
   . ./env.sh set main    # for mainnet
   . ./env.sh set test    # for testnet
   . ./env.sh set sim     # for simulator
   ```

### Running Individual Bots

```bash
# Example: Run the announcer configure bot
poetry run python -m keeper_bots.announcer_configure_bot

# Example: Run the liquidation start bot
poetry run python -m keeper_bots.liquidation_start_bot

# Example: Run the oracle update bot
poetry run python -m keeper_bots.oracle_update_bot
```

### Environment-Specific Execution

Make sure to configure the appropriate environment before running bots:

```bash
# For testnet operations
. ./env.sh set test
export PRIVATE_KEY="your_testnet_private_key"
poetry run python -m keeper_bots.announcer_update_bot

# For mainnet operations
. ./env.sh set main
export PRIVATE_KEY="your_mainnet_private_key"
poetry run python -m keeper_bots.liquidation_start_bot
```

## Docker Image Building

### Building the Image

Build the Docker image using the provided Dockerfile:

```bash
# Basic build for mainnet
docker build -t keeper-bots:latest .

# Build for testnet
docker build --build-arg CHIA_NETWORK=testnet -t keeper-bots:testnet .

# Build with custom tag
docker build -t your-registry/keeper-bots:v1.0.0 .
```

### Docker Build Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `CHIA_NETWORK` | Chia network configuration (mainnet/testnet) | mainnet |

### Running with Docker

```bash
# Run a specific bot with Docker
docker run -e PRIVATE_KEY="your_key" \
           -e RPC_URL="https://testnet-api.circuitdao.com" \
           -e ADD_SIG_DATA="your_add_sig_data" \
           -e FEE_PER_COST="7" \
           -e CHIA_NETWORK="testnet" \
           keeper-bots:latest \
           python -m keeper_bots.announcer_configure_bot
```

### Docker Environment Variables

When running with Docker, ensure these environment variables are set:

- `PRIVATE_KEY`: Your private key for signing transactions
- `RPC_URL`: Circuit API endpoint
- `ADD_SIG_DATA`: Additional signature data
- `FEE_PER_COST`: Fee per cost unit
- `CHIA_NETWORK`: Network configuration (mainnet/testnet)
- `CHIA_ROOT`: Chia configuration directory (default: /app/.chia)

### Health Check

The Docker image includes a built-in health check that verifies:
- Required environment variables are set
- Keeper bots module can be imported
- Basic system readiness

Access the health check:
```bash
docker run keeper-bots:latest python /app/healthcheck.py
```

## Development

### Local Development Setup

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd keeper-bots
   ```

2. **Install Poetry** (if not already installed):
   ```bash
   curl -sSL https://install.python-poetry.org | python3 -
   ```

3. **Install dependencies**:
   ```bash
   poetry install
   ```

4. **Configure environment**:
   ```bash
   # Set up for testnet development
   . ./env.sh set test
   export PRIVATE_KEY="your_development_private_key"
   ```

5. **Run tests** (if available):
   ```bash
   poetry run pytest
   ```

### Code Structure

- `keeper_bots/`: Main bot implementations
- `env.sh`: Environment configuration script
- `Dockerfile`: Multi-stage Docker build configuration
- `pyproject.toml`: Python project configuration and dependencies
- `log_conf.yaml`: Logging configuration
- `logging_filters.py`: Custom logging filters

### Adding New Bots

To add a new bot:

1. Create a new Python file in the `keeper_bots/` directory
2. Implement your bot logic following existing patterns
3. Ensure proper error handling and logging
4. Test with the simulator environment first
5. Update this README with the new bot information

## Troubleshooting

### Common Issues

1. **Environment Variables Not Set**:
   ```bash
   # Check current environment
   . ./env.sh show
   
   # Ensure PRIVATE_KEY is set
   echo $PRIVATE_KEY
   ```

2. **Network Connection Issues**:
   - Verify RPC_URL is accessible
   - Check network connectivity
   - Ensure correct network configuration

3. **Private Key Issues**:
   - Ensure private key format is correct
   - Verify private key has sufficient permissions
   - Check private key corresponds to correct network

4. **Docker Build Issues**:
   - Ensure Docker is running
   - Check available disk space
   - Verify network connectivity for dependency downloads

### Logging

Bots use structured logging configured in `log_conf.yaml`. Logs include:
- Timestamp
- Log level
- Bot name
- Message details

To adjust logging levels, modify the `log_conf.yaml` file or set environment variables as needed.

## License

See [LICENCE](LICENCE) file for license information.