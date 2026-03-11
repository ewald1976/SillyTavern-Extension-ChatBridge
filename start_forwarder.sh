#!/bin/bash
# ChatBridge Forwarder startup script
# Usage: ./start_forwarder.sh

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${GREEN}\u2554$(printf '\u2550%.0s' {1..44})\u2557${NC}"
echo -e "${GREEN}\u2551        ChatBridge Forwarder                \u2551${NC}"
echo -e "${GREEN}\u255a$(printf '\u2550%.0s' {1..44})\u255d${NC}"
echo ""

CONDA_ENV_NAME="chatbridge"

if command -v conda &> /dev/null; then
    echo -e "${BLUE}Conda detected${NC}"
    if [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
        source "$HOME/miniconda3/etc/profile.d/conda.sh"
    elif [ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then
        source "$HOME/anaconda3/etc/profile.d/conda.sh"
    elif [ -f "/opt/miniconda3/etc/profile.d/conda.sh" ]; then
        source "/opt/miniconda3/etc/profile.d/conda.sh"
    fi
    if conda env list | grep -q "^$CONDA_ENV_NAME "; then
        echo -e "${GREEN}[OK]${NC} Using existing Conda environment: $CONDA_ENV_NAME"
        conda activate "$CONDA_ENV_NAME"
    else
        echo -e "${YELLOW}[..] Creating new Conda environment: $CONDA_ENV_NAME${NC}"
        conda create -n "$CONDA_ENV_NAME" python=3.10 -y
        conda activate "$CONDA_ENV_NAME"
        echo -e "${GREEN}[OK]${NC} Conda environment created"
    fi
    PYTHON_CMD=python
    USE_CONDA=true
else
    echo -e "${YELLOW}[!!] Conda not found, using system Python${NC}"
    USE_CONDA=false
    if command -v python3 &> /dev/null; then
        PYTHON_CMD=python3
    elif command -v python &> /dev/null; then
        PYTHON_CMD=python
    else
        echo -e "${RED}[ERR] Python not found${NC}"
        echo "Install Miniconda: brew install miniconda"
        echo "or: https://docs.conda.io/en/latest/miniconda.html"
        exit 1
    fi
fi

echo -e "${YELLOW}[..] Checking environment...${NC}"
echo "     Python: $($PYTHON_CMD --version 2>&1)"

echo -e "${YELLOW}[..] Checking dependencies...${NC}"
REQUIRED_PACKAGES=("aiohttp" "websockets")
MISSING_PACKAGES=()
for package in "${REQUIRED_PACKAGES[@]}"; do
    if ! $PYTHON_CMD -c "import $package" &> /dev/null; then
        MISSING_PACKAGES+=("$package")
        echo -e "     ${RED}[!!]${NC} $package (missing)"
    else
        echo -e "     ${GREEN}[OK]${NC} $package"
    fi
done

if [ ${#MISSING_PACKAGES[@]} -ne 0 ]; then
    echo ""
    echo -e "${YELLOW}[..] Installing missing packages...${NC}"
    if [ "$USE_CONDA" = true ]; then
        $PYTHON_CMD -m pip install "${MISSING_PACKAGES[@]}"
    else
        $PYTHON_CMD -m pip install --user "${MISSING_PACKAGES[@]}" 2>&1 | grep -v "externally-managed-environment" || {
            echo -e "${RED}[ERR] Cannot install packages${NC}"
            echo "Recommended:"
            echo "1. Install Miniconda: brew install miniconda"
            echo "2. Or use a venv:     python3 -m venv .venv && source .venv/bin/activate"
            exit 1
        }
    fi
    echo ""
fi

if [ ! -f "settings.json" ]; then
    echo -e "${YELLOW}[!!] settings.json not found -- copy settings.json.template and configure it${NC}"
    echo ""
fi

echo -e "${YELLOW}[..] Checking ports...${NC}"
BRIDGE_PORT=8001
API_PORT=8003
for PORT in $BRIDGE_PORT $API_PORT; do
    if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
        echo -e "     ${RED}[!!]${NC} Port $PORT already in use  -->  lsof -i :$PORT"
        exit 1
    else
        echo -e "     ${GREEN}[OK]${NC} Port $PORT available"
    fi
done

echo ""
echo -e "${GREEN}[OK] All checks passed -- starting Forwarder...${NC}"
echo -e "${YELLOW}--------------------------------------------------${NC}"
echo ""
echo -e "${YELLOW}Info:${NC}"
echo "     WebSocket bridge port : $BRIDGE_PORT"
echo "     User API port         : $API_PORT"
echo "     Stop with Ctrl+C"
echo ""
echo -e "${YELLOW}--------------------------------------------------${NC}"
echo ""

$PYTHON_CMD ChatBridge_APIHijackForwarder.py

EXIT_CODE=$?
echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}[OK] Forwarder exited normally${NC}"
else
    echo -e "${RED}[!!] Forwarder exited with error (code: $EXIT_CODE)${NC}"
fi
