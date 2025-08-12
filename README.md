# Keeper Bots

Keeper bots for the Circuit protocol that handle price announcements and oracle updates.

## Overview

The keeper bots system consists of three specialized bots that work together:

1. **announcer_configure_bot** - Configures price announcers on the blockchain
2. **announcer_update_bot** - Updates price feeds from external sources (OKX)
3. **oracle_update_bot** - Updates oracle prices on the blockchain

## Architecture

The bots are deployed as Docker containers on Google Cloud Platform using:
- **Compute Engine VMs** for running the containers
- **Google Secret Manager** for secure private key storage
- **Terraform/Terragrunt** for infrastructure deployment

## Private Key Configuration

### ⚠️ Important: How Private Keys Work

The keeper bots use a **Secret Manager flow**, NOT direct environment variables in containers.

**Correct Flow:**
```
Environment Variable (PRICE_ANNOUNCER_PRIVATE_KEY) →
Terragrunt → 
Terraform → 
Secret Manager → 
VM Startup Script → 
Container Environment (PRIVATE_KEY)
```

**❌ This DOES NOT work:**
```bash
# Don't try to set PRIVATE_KEY directly in the container
export PRIVATE_KEY="your_key"
docker run -e PRIVATE_KEY="your_key" ...
```

**✅ This is the correct way:**
```bash
# Set the environment variable before terragrunt deployment
export PRICE_ANNOUNCER_PRIVATE_KEY="your_actual_private_key"
cd infra/chia-terragrunt/environments/testnet/price-announcers/announcer-1
terragrunt apply
```

### Setup Process

1. **Set Environment Variables** (before deployment):
   ```bash
   export PRICE_ANNOUNCER_PRIVATE_KEY="your_actual_private_key_here"
   export PRICE_ANNOUNCER_ADD_SIG_DATA="your_add_sig_data_here"
   ```

2. **Deploy Infrastructure**:
   ```bash
   cd infra/chia-terragrunt/environments/testnet/price-announcers/announcer-1
   terragrunt apply
   ```

3. **Verify Deployment**:
   ```bash
   # Check if VM was created
   gcloud compute instances list --filter="name:keeper-bots-testnet-announcer-1"
   
   # SSH to VM and check containers
   gcloud compute ssh keeper-bots-testnet-announcer-1 --zone=europe-west1-b
   docker ps
   docker logs keeper-bot-announcer-configure
   ```

## Troubleshooting Private Key Issues

If you're experiencing private key access problems, use the troubleshooting script:

```bash
# Run the private key diagnostic script
./test_private_key_flow.sh
```

This script checks:
- ✅ Local environment variables
- ✅ Secret Manager contents
- ✅ VM and container status
- ✅ Common configuration issues

### Common Problems

1. **"I set PRIVATE_KEY as env var but it doesn't work"**
   - The system uses Secret Manager, not direct env vars
   - Set `PRICE_ANNOUNCER_PRIVATE_KEY` before `terragrunt apply`

2. **"Secret contains placeholder value"**
   - You deployed without setting the real private key
   - Set the env var and redeploy with `terragrunt apply`

3. **"Container can't access private key"**
   - Check Secret Manager permissions (handled by Terraform)
   - Verify the secret exists and contains the correct value

## Automated Deployment

The keeper bots project includes an automated deploy script that handles version management, Docker builds, and deployment coordination.

### Quick Start

```bash
# Basic deployment (increments patch version automatically)
./deploy.sh

# Deploy with minor version increment
./deploy.sh --type minor

# Deploy specific version
./deploy.sh --version 2.1.0

# Deploy and update infrastructure defaults
./deploy.sh --update-infra

# Dry run to see what would happen
./deploy.sh --dry-run
```

### Deploy Script Features

- **Automatic Version Management**: Increments versions in `pyproject.toml` (patch, minor, major)
- **Docker Integration**: Uses existing `build.sh` for consistent Docker operations
- **Git Integration**: Automatically commits version changes and creates git tags
- **Infrastructure Updates**: Optionally updates default versions in Terragrunt modules
- **Dry Run Support**: Test deployment process without making changes
- **Comprehensive Validation**: Checks environment and prerequisites

### Deploy Script Options

```bash
./deploy.sh [OPTIONS]

Options:
  -t, --type TYPE               Version increment type: patch, minor, major (default: patch)
  -v, --version VERSION         Use specific version instead of auto-increment
  -p, --project-id PROJECT_ID   Google Cloud Project ID (default: nice-azimuth-452909-k7)
  -u, --update-infra           Update infrastructure default version
  -s, --skip-build             Skip Docker build and push
  -d, --dry-run                Show what would be done without executing
  -h, --help                   Show help message
```

### Deployment Workflow

1. **Version Management**: Script reads current version from `pyproject.toml` and increments it
2. **Docker Build**: Integrates with `build.sh` to build and push versioned images
3. **Git Operations**: Commits version changes and creates git tags
4. **Infrastructure**: Optionally updates default versions in Terragrunt modules

### Examples

```bash
# Standard patch release (0.1.0 → 0.1.1)
./deploy.sh

# Minor release with infrastructure update (0.1.1 → 0.2.0)
./deploy.sh --type minor --update-infra

# Major release to different project
./deploy.sh --type major --project-id my-production-project

# Deploy specific version without building (if image exists)
./deploy.sh --version 1.5.0 --skip-build

# Test deployment process
./deploy.sh --dry-run --update-infra
```

### After Deployment

After running the deploy script:

1. **Push Changes**: `git push && git push --tags`
2. **Update Environments**: Update specific environment configurations if needed
3. **Deploy Infrastructure**: Run `terragrunt apply` in your environment directories
4. **Monitor**: Check deployment logs and service health

## Development

### Building the Docker Image

```bash
# Use the automated deploy script (recommended)
./deploy.sh

# Or use the build script directly
./build.sh

# Or manually:
docker build --platform linux/amd64 -t gcr.io/PROJECT_ID/keeper-bots:latest .
docker push gcr.io/PROJECT_ID/keeper-bots:latest
```

### Local Development

For local development, you can run the bots directly:

```bash
# Install dependencies
poetry install

# Run a specific bot
poetry run python -m keeper_bots.announcer_configure_bot
```

### Environment Variables for Local Development

When running locally, you can set these environment variables:

```bash
export PRIVATE_KEY="your_private_key"
export RPC_URL="https://testnet-api.circuitdao.com"  
export ENVIRONMENT="testnet"
export ADD_SIG_DATA="your_add_sig_data"
export CHIA_NETWORK="testnet"
export CHIA_ROOT="$HOME/.chia"
```

## Deployment Documentation

For detailed deployment instructions, see:
- `/infra/chia-terragrunt/environments/testnet/price-announcers/README.md`
- `/infra/chia-terragrunt/PRICE_ANNOUNCERS_DEPLOYMENT.md`

## Support

If you encounter issues:
1. Run the troubleshooting script: `./test_private_key_flow.sh`
2. Check container logs on the VM
3. Verify Secret Manager contents
4. Ensure environment variables are set before deployment