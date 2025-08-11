#!/bin/bash

# Keeper Bots Docker Build Script
# This script builds and pushes the keeper-bots Docker image with proper platform specification
# Similar to the circuit project's build process for consistency

set -euo pipefail

# Configuration
DEFAULT_PROJECT_ID="nice-azimuth-452909-k7"
DEFAULT_TAG="latest"

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
    echo "  -p, --project-id PROJECT_ID    Google Cloud Project ID (default: $DEFAULT_PROJECT_ID)"
    echo "  -t, --tag TAG                  Docker image tag (default: $DEFAULT_TAG)"
    echo "  -s, --skip-push               Skip pushing to registry"
    echo "  -d, --dry-run                 Show what would be done without executing"
    echo "  -h, --help                    Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                            # Build and push with defaults"
    echo "  $0 -p my-project -t v1.0.0    # Build with custom project and tag"
    echo "  $0 -s                         # Build only, don't push"
    echo "  $0 -d                         # Dry run to see what would happen"
}

# Default values
PROJECT_ID="$DEFAULT_PROJECT_ID"
TAG="$DEFAULT_TAG"
SKIP_PUSH=false
DRY_RUN=false

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -p|--project-id)
            PROJECT_ID="$2"
            shift 2
            ;;
        -t|--tag)
            TAG="$2"
            shift 2
            ;;
        -s|--skip-push)
            SKIP_PUSH=true
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

# Derived values
IMAGE_REPO="gcr.io/$PROJECT_ID/keeper-bots"
FULL_IMAGE_TAG="$IMAGE_REPO:$TAG"

# Validation
if [[ -z "$PROJECT_ID" ]]; then
    echo -e "${RED}Error: Project ID is required${NC}"
    exit 1
fi

# Check if we're in the right directory
if [[ ! -f "Dockerfile" ]]; then
    echo -e "${RED}Error: Dockerfile not found. Please run this script from the keeper-bots directory.${NC}"
    exit 1
fi

# Function to build Docker image
build_image() {
    if [[ "$DRY_RUN" == true ]]; then
        echo -e "${BLUE}[DRY RUN] Would build Docker image:${NC}"
        echo -e "${BLUE}  Command: docker build --platform linux/amd64 -t $FULL_IMAGE_TAG .${NC}"
        return 0
    fi
    
    echo -e "${YELLOW}🔨 Building Docker image with AMD64 platform...${NC}"
    echo -e "${YELLOW}   Image: $FULL_IMAGE_TAG${NC}"
    
    docker build --platform linux/amd64 -t "$FULL_IMAGE_TAG" .
    
    if [[ $? -eq 0 ]]; then
        echo -e "${GREEN}✓ Docker image built successfully${NC}"
    else
        echo -e "${RED}✗ Docker build failed${NC}"
        exit 1
    fi
}

# Function to push Docker image
push_image() {
    if [[ "$SKIP_PUSH" == true ]]; then
        echo -e "${YELLOW}⏭️  Skipping Docker push (--skip-push flag used)${NC}"
        return 0
    fi
    
    if [[ "$DRY_RUN" == true ]]; then
        echo -e "${BLUE}[DRY RUN] Would push Docker image:${NC}"
        echo -e "${BLUE}  Command: docker push $FULL_IMAGE_TAG${NC}"
        return 0
    fi
    
    echo -e "${YELLOW}📤 Pushing Docker image...${NC}"
    docker push "$FULL_IMAGE_TAG"
    
    if [[ $? -eq 0 ]]; then
        echo -e "${GREEN}✓ Docker image pushed successfully${NC}"
    else
        echo -e "${RED}✗ Docker push failed${NC}"
        exit 1
    fi
}

# Main execution
main() {
    echo -e "${BLUE}🚀 Keeper Bots Docker Build${NC}"
    echo -e "${BLUE}============================${NC}"
    echo ""
    echo -e "${YELLOW}📋 Configuration:${NC}"
    echo -e "${YELLOW}   Project ID: $PROJECT_ID${NC}"
    echo -e "${YELLOW}   Image Tag: $TAG${NC}"
    echo -e "${YELLOW}   Full Image: $FULL_IMAGE_TAG${NC}"
    echo -e "${YELLOW}   Platform: linux/amd64${NC}"
    if [[ "$SKIP_PUSH" == true ]]; then
        echo -e "${YELLOW}   Push: Disabled${NC}"
    fi
    if [[ "$DRY_RUN" == true ]]; then
        echo -e "${YELLOW}   Mode: DRY RUN${NC}"
    fi
    echo ""
    
    # Build the image
    build_image
    
    # Push the image (if not skipped)
    push_image
    
    echo ""
    echo -e "${GREEN}🎉 Build process completed successfully!${NC}"
    echo ""
    echo -e "${BLUE}Next steps:${NC}"
    echo -e "${BLUE}1. Update your deployment configuration to use: $FULL_IMAGE_TAG${NC}"
    echo -e "${BLUE}2. Deploy using terragrunt in your environment directory${NC}"
    echo -e "${BLUE}3. Monitor the deployment logs for successful startup${NC}"
}

# Run main function
main