#!/usr/bin/env bash
# =============================================================================
# Hermes-Pi Factory Guardian — Hermes Agent Installation Script
# =============================================================================
# Installs Hermes Agent by Nous Research and registers custom factory skills.
#
# Usage: sudo ./scripts/install_hermes.sh [--provider ollama|groq] [--model MODEL]
# =============================================================================

set -euo pipefail

# ── Color output ───────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "\n${BLUE}━━━ $* ━━━${NC}"; }

# ── Defaults ───────────────────────────────────────────────────────────
HERMES_DIR="/opt/hermes-agent"
PROJECT_DIR="/opt/hermes-pi-factory-guardian"
PROVIDER="ollama"
MODEL="llama3"

# ── Parse arguments ────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case $1 in
        --provider)
            PROVIDER="$2"
            shift 2
            ;;
        --model)
            MODEL="$2"
            shift 2
            ;;
        *)
            log_error "Unknown argument: $1"
            echo "Usage: $0 [--provider ollama|groq] [--model MODEL]"
            exit 1
            ;;
    esac
done

# ── Check running as root ──────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    log_error "This script must be run as root (use sudo)."
    exit 1
fi

# =============================================================================
log_step "Step 1/5: Install Ollama (Local LLM Provider)"
# =============================================================================
if command -v ollama &>/dev/null; then
    log_info "Ollama already installed: $(ollama --version 2>/dev/null || echo 'version unknown')"
else
    log_info "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
    log_info "Ollama installed successfully"
fi

# Enable Ollama service
systemctl enable ollama 2>/dev/null || true
systemctl start ollama 2>/dev/null || true

# Wait for Ollama to be ready
log_info "Waiting for Ollama to start..."
for i in $(seq 1 30); do
    if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
        log_info "Ollama is ready"
        break
    fi
    if [[ $i -eq 30 ]]; then
        log_warn "Ollama did not start within 30 seconds"
    fi
    sleep 1
done

# =============================================================================
log_step "Step 2/5: Pull LLM Model"
# =============================================================================
log_info "Pulling model: ${MODEL}..."
if ollama list 2>/dev/null | grep -q "$MODEL"; then
    log_info "Model '${MODEL}' already available"
else
    ollama pull "$MODEL"
    log_info "Model '${MODEL}' pulled successfully"
fi

# Verify model works
log_info "Testing model inference..."
response=$(ollama run "$MODEL" "Say 'hello' in one word" --nowordwrap 2>/dev/null || echo "")
if [[ -n "$response" ]]; then
    log_info "Model inference test: ✅ PASSED"
else
    log_warn "Model inference test: ⚠️  FAILED (may need more time to load)"
fi

# =============================================================================
log_step "Step 3/5: Install Hermes Agent"
# =============================================================================
if [[ -d "$HERMES_DIR" ]]; then
    log_info "Hermes Agent directory exists: $HERMES_DIR"
    log_info "Updating..."
    cd "$HERMES_DIR"
    git pull --ff-only 2>/dev/null || log_warn "Git pull failed (may not be a git repo)"
else
    log_info "Cloning Hermes Agent from Nous Research..."
    git clone https://github.com/nousresearch/hermes-agent.git "$HERMES_DIR" 2>/dev/null || {
        log_warn "Could not clone Hermes Agent (repository may not exist yet)"
        log_warn "Creating placeholder directory..."
        mkdir -p "$HERMES_DIR"
        mkdir -p "${HERMES_DIR}/skills"
    }
fi

# Install Hermes Agent dependencies
if [[ -f "${HERMES_DIR}/requirements.txt" ]]; then
    log_info "Installing Hermes Agent Python dependencies..."
    "${PROJECT_DIR}/venv/bin/pip" install -r "${HERMES_DIR}/requirements.txt" -q 2>/dev/null || \
        log_warn "Some Hermes dependencies failed to install"
elif [[ -f "${HERMES_DIR}/pyproject.toml" ]]; then
    log_info "Installing Hermes Agent..."
    "${PROJECT_DIR}/venv/bin/pip" install -e "${HERMES_DIR}" -q 2>/dev/null || \
        log_warn "Hermes Agent install failed"
else
    log_warn "No requirements.txt or pyproject.toml found in Hermes Agent dir"
    log_info "Creating minimal Hermes Agent configuration..."

    # Create a minimal hermes config
    mkdir -p "${HERMES_DIR}/config"
    cat > "${HERMES_DIR}/config/hermes.yaml" << EOF
# Hermes Agent Configuration
provider: ${PROVIDER}
model: ${MODEL}

${PROVIDER}:
  base_url: http://localhost:11434
  model: ${MODEL}

skills_dir: ${HERMES_DIR}/skills
learning:
  enabled: true
  save_dir: ${HERMES_DIR}/learned_skills
EOF
fi

# =============================================================================
log_step "Step 4/5: Register Custom Factory Skills"
# =============================================================================
SKILLS_SRC="${PROJECT_DIR}/skills"
SKILLS_DST="${HERMES_DIR}/skills"

mkdir -p "$SKILLS_DST"

log_info "Registering factory monitoring skills with Hermes Agent..."

SKILL_NAMES=(
    "anomaly-detection"
    "alert-router"
    "shift-report"
    "sensor-polling"
    "vision-monitor"
    "learning-loop"
)

for skill_name in "${SKILL_NAMES[@]}"; do
    src_dir="${SKILLS_SRC}/${skill_name}"
    dst_dir="${SKILLS_DST}/${skill_name}"

    if [[ -d "$src_dir" ]]; then
        # Copy skill definition
        if [[ -f "${src_dir}/description.md" ]]; then
            cp "${src_dir}/description.md" "${dst_dir}/" 2>/dev/null || {
                mkdir -p "$dst_dir"
                cp "${src_dir}/description.md" "${dst_dir}/"
            }
            log_info "  ✅ Registered: ${skill_name}"
        else
            log_warn "  ⚠️  No description.md found for: ${skill_name}"
        fi

        # Copy Python implementation
        if [[ -f "${src_dir}"/*.py ]]; then
            cp "${src_dir}"/*.py "${dst_dir}/" 2>/dev/null || true
            log_info "     └─ Python module copied"
        fi
    else
        log_warn "  ⚠️  Skill directory not found: ${src_dir}"
    fi
done

# =============================================================================
log_step "Step 5/5: Verify Installation"
# =============================================================================
log_info "Running verification checks..."

# Check Hermes Agent
if [[ -f "${HERMES_DIR}/hermes.py" ]] || [[ -f "${HERMES_DIR}/main.py" ]] || [[ -d "${HERMES_DIR}/src" ]]; then
    log_info "  ✅ Hermes Agent installed at: ${HERMES_DIR}"
else
    log_warn "  ⚠️  Hermes Agent main entry point not found"
fi

# Check skills
skill_count=0
for skill_name in "${SKILL_NAMES[@]}"; do
    if [[ -f "${SKILLS_DST}/${skill_name}/description.md" ]]; then
        skill_count=$((skill_count + 1))
    fi
done
log_info "  ✅ ${skill_count}/${#SKILL_NAMES[@]} skills registered"

# Check Ollama
if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
    log_info "  ✅ Ollama running at http://localhost:11434"
else
    log_warn "  ⚠️  Ollama not responding (may need: sudo systemctl start ollama)"
fi

# Check model
if ollama list 2>/dev/null | grep -q "$MODEL"; then
    log_info "  ✅ Model '${MODEL}' available"
else
    log_warn "  ⚠️  Model '${MODEL}' not found (run: ollama pull ${MODEL})"
fi

# ── Environment variables reminder ─────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║          Hermes Agent Installation Complete                 ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║                                                              ║"
echo "║  Provider:    ${PROVIDER}                                     "
echo "║  Model:       ${MODEL}                                        "
echo "║  Skills:      ${skill_count} registered                        "
echo "║                                                              ║"
echo "║  SET THESE ENVIRONMENT VARIABLES:                            ║"
echo "║                                                              ║"
echo "║  # Telegram (optional):                                       ║"
echo "║  export TELEGRAM_BOT_TOKEN=\"your_bot_token\"                  "
echo "║  export TELEGRAM_CHAT_ID=\"your_chat_id\"                      "
echo "║                                                              ║"
echo "║  # Slack (optional):                                          ║"
echo "║  export SLACK_WEBHOOK_URL=\"https://hooks.slack.com/...\"     "
echo "║                                                              ║"
echo "║  # Groq Cloud (if using instead of Ollama):                   ║"
echo "║  export GROQ_API_KEY=\"your_groq_key\"                        ║"
echo "║                                                              ║"
echo "║  Add to /etc/environment or ~/.bashrc for persistence.        ║"
echo "║                                                              ║"
echo "║  START:  sudo systemctl start hermes-guardian                 ║"
echo "║  LOGS:   journalctl -u hermes-guardian -f                     ║"
echo "║                                                              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
