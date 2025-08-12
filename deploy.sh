#!/bin/bash

# Keeper Bots Deploy Script
# This script automates version management, Docker build, and deployment for keeper-bots
# It integrates with the existing build.sh script and optionally updates infrastructure defaults

set -euo pipefail

# Configuration
DEFAULT_PROJECT_ID="nice-azimuth-452909-k7"
INFRASTRUCTURE_PATH="../infra/chia-terragrunt/modules/keeper-bots/variables.tf"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Usage function
usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -t, --type TYPE               Version increment type: patch, minor, major (default: patch)"
    echo "  -v, --version VERSION         Use specific version instead of auto-increment"
    echo "  -p, --project-id PROJECT_ID   Google Cloud Project ID (default: $DEFAULT_PROJECT_ID)"
    echo "  -u, --update-infra           Update infrastructure default version"
    echo "  -s, --skip-build             Skip Docker build and push"
    echo "  -d, --dry-run                Show what would be done without executing"
    echo "  -h, --help                   Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                           # Increment patch version and deploy"
    echo "  $0 -t minor                  # Increment minor version"
    echo "  $0 -v 2.1.0                 # Use specific version"
    echo "  $0 -u                        # Also update infrastructure default"
    echo "  $0 -d                        # Dry run to see what would happen"
}

# Default values
VERSION_TYPE="patch"
CUSTOM_VERSION=""
PROJECT_ID="$DEFAULT_PROJECT_ID"
UPDATE_INFRA=false
SKIP_BUILD=false
DRY_RUN=false

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -t|--type)
            VERSION_TYPE="$2"
            shift 2
            ;;
        -v|--version)
            CUSTOM_VERSION="$2"
            shift 2
            ;;
        -p|--project-id)
            PROJECT_ID="$2"
            shift 2
            ;;
        -u|--update-infra)
            UPDATE_INFRA=true
            shift
            ;;
        -s|--skip-build)
            SKIP_BUILD=true
            shift
            ;;
        -d|--dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Validation
if [[ ! "$VERSION_TYPE" =~ ^(patch|minor|major)$ ]] && [[ -z "$CUSTOM_VERSION" ]]; then
    echo -e "${RED}Error: Version type must be patch, minor, or major${NC}"
    exit 1
fi

# Check if we're in the right directory
if [[ ! -f "pyproject.toml" ]]; then
    echo -e "${RED}Error: pyproject.toml not found. Please run this script from the keeper-bots directory.${NC}"
    exit 1
fi

if [[ ! -f "build.sh" ]]; then
    echo -e "${RED}Error: build.sh not found. Please run this script from the keeper-bots directory.${NC}"
    exit 1
fi

# Function to get current version from pyproject.toml
get_current_version() {
    grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/'
}

# Function to increment version
increment_version() {
    local version=$1
    local type=$2
    
    IFS='.' read -ra parts <<< "$version"
    local major=${parts[0]}
    local minor=${parts[1]}
    local patch=${parts[2]}
    
    case $type in
        patch)
            patch=$((patch + 1))
            ;;
        minor)
            minor=$((minor + 1))
            patch=0
            ;;
        major)
            major=$((major + 1))
            minor=0
            patch=0
            ;;
    esac
    
    echo "${major}.${minor}.${patch}"
}

# Function to update version in pyproject.toml
update_pyproject_version() {
    local new_version=$1
    
    if [[ "$DRY_RUN" == true ]]; then
        echo -e "${BLUE}[DRY RUN] Would update pyproject.toml version to: $new_version${NC}"
        return 0
    fi
    
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        sed -i '' "s/^version = .*/version = \"$new_version\"/" pyproject.toml
    else
        # Linux
        sed -i "s/^version = .*/version = \"$new_version\"/" pyproject.toml
    fi
}

# Function to update infrastructure default version
update_infrastructure_version() {
    local new_version=$1
    
    if [[ ! -f "$INFRASTRUCTURE_PATH" ]]; then
        echo -e "${YELLOW}⚠️  Infrastructure file not found at: $INFRASTRUCTURE_PATH${NC}"
        echo -e "${YELLOW}   Skipping infrastructure update${NC}"
        return 0
    fi
    
    if [[ "$DRY_RUN" == true ]]; then
        echo -e "${BLUE}[DRY RUN] Would update infrastructure default version to: $new_version${NC}"
        return 0
    fi
    
    echo -e "${YELLOW}📝 Updating infrastructure default version...${NC}"
    
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        sed -i '' "s/default     = \"[0-9]*\.[0-9]*\.[0-9]*\"/default     = \"$new_version\"/" "$INFRASTRUCTURE_PATH"
    else
        # Linux
        sed -i "s/default     = \"[0-9]*\.[0-9]*\.[0-9]*\"/default     = \"$new_version\"/" "$INFRASTRUCTURE_PATH"
    fi
    
    echo -e "${GREEN}✓ Infrastructure default version updated${NC}"
}

# Function to build and push Docker image
build_and_push() {
    local version=$1
    
    if [[ "$SKIP_BUILD" == true ]]; then
        echo -e "${YELLOW}⏭️  Skipping Docker build and push (--skip-build flag used)${NC}"
        return 0
    fi
    
    echo -e "${YELLOW}🔨 Building and pushing Docker image...${NC}"
    
    local build_args="--project-id $PROJECT_ID --tag $version"
    if [[ "$DRY_RUN" == true ]]; then
        build_args="$build_args --dry-run"
    fi
    
    ./build.sh $build_args
}

# Function to commit version changes
commit_changes() {
    local new_version=$1
    
    if [[ "$DRY_RUN" == true ]]; then
        echo -e "${BLUE}[DRY RUN] Would commit version changes with message: 'Bump version to $new_version'${NC}"
        return 0
    fi
    
    echo -e "${YELLOW}📝 Committing version changes...${NC}"
    
    git add pyproject.toml

    git commit -m "Bump version to $new_version" || {
        echo -e "${YELLOW}⚠️  No changes to commit (version might already be current)${NC}"
    }
    
    # Create git tag
    git tag -a "v$new_version" -m "Version $new_version" || {
        echo -e "${YELLOW}⚠️  Tag v$new_version might already exist${NC}"
    }
    
    echo -e "${GREEN}✓ Version changes committed and tagged${NC}"
}

# Main execution
main() {
    echo -e "${BLUE}🚀 Keeper Bots Deploy${NC}"
    echo -e "${BLUE}====================${NC}"
    echo ""
    
    # Get current version
    current_version=$(get_current_version)
    echo -e "${YELLOW}📋 Current version: $current_version${NC}"
    
    # Determine new version
    if [[ -n "$CUSTOM_VERSION" ]]; then
        new_version="$CUSTOM_VERSION"
        echo -e "${YELLOW}📋 Using custom version: $new_version${NC}"
    else
        new_version=$(increment_version "$current_version" "$VERSION_TYPE")
        echo -e "${YELLOW}📋 New version ($VERSION_TYPE increment): $new_version${NC}"
    fi
    
    echo -e "${YELLOW}📋 Project ID: $PROJECT_ID${NC}"
    echo -e "${YELLOW}📋 Image: gcr.io/$PROJECT_ID/keeper-bots:$new_version${NC}"
    
    if [[ "$UPDATE_INFRA" == true ]]; then
        echo -e "${YELLOW}📋 Will update infrastructure defaults${NC}"
    fi
    
    if [[ "$SKIP_BUILD" == true ]]; then
        echo -e "${YELLOW}📋 Will skip Docker build${NC}"
    fi
    
    if [[ "$DRY_RUN" == true ]]; then
        echo -e "${YELLOW}📋 Mode: DRY RUN${NC}"
    fi
    echo ""
    
    # Update version in pyproject.toml
    echo -e "${YELLOW}📝 Updating pyproject.toml version...${NC}"
    update_pyproject_version "$new_version"
    if [[ "$DRY_RUN" != true ]]; then
        echo -e "${GREEN}✓ pyproject.toml updated${NC}"
    fi
    
    # Update infrastructure version if requested
    if [[ "$UPDATE_INFRA" == true ]]; then
        update_infrastructure_version "$new_version"
    fi
    
    # Build and push Docker image
    build_and_push "$new_version"
    
    # Commit changes
    commit_changes "$new_version"
    
    echo ""
    echo -e "${GREEN}🎉 Deploy process completed successfully!${NC}"
    echo ""
    echo -e "${BLUE}📦 New version: $new_version${NC}"
    echo -e "${BLUE}🐳 Docker image: gcr.io/$PROJECT_ID/keeper-bots:$new_version${NC}"
    echo ""
    echo -e "${BLUE}Next steps:${NC}"
    echo -e "${BLUE}1. Push your commits and tags: git push && git push --tags${NC}"
    echo -e "${BLUE}2. Update your deployment configuration to use the new version${NC}"
    echo -e "${BLUE}3. Deploy using terragrunt in your environment directory${NC}"
    echo -e "${BLUE}4. Monitor the deployment logs for successful startup${NC}"
    
    if [[ "$UPDATE_INFRA" != true ]]; then
        echo ""
        echo -e "${YELLOW}💡 Tip: Use --update-infra flag to automatically update infrastructure defaults${NC}"
    fi
}

# Run main function
main