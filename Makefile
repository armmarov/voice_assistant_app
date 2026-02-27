# ─── Voice Assistant — Makefile ───────────────────────────────────────────────
#
# Targets:
#   make build            Build binary natively on Jetson Orin via SSH
#   make run              Run from source locally (dev/testing)
#   make deploy           Build on Orin + deploy binary + assets
#   make install          Install binary + service on Orin (via ssh)
#   make service-start    Start systemd service on Orin (via ssh)
#   make service-stop     Stop systemd service on Orin (via ssh)
#   make service-logs     Tail service logs on Orin (via ssh)
#   make setup-build-deps Install build deps on Orin (one-time, via ssh)
#   make setup-robot      Install runtime deps on Orin (one-time, via ssh)
#   make clean            Remove local build artifacts
#
# Orin connection — override on the command line:
#   make deploy ROBOT_HOST=192.168.1.100 ROBOT_USER=ubuntu
# ──────────────────────────────────────────────────────────────────────────────

BINARY      := voice_assistant
SRC         := voice_assistant.py
DIST_DIR    := dist
SERVICE_SRC := voice_assistant.service

# Orin SSH target — override as needed
ROBOT_USER  ?= ubuntu
ROBOT_HOST  ?= 192.168.0.63
ROBOT_DIR   ?= /opt/voice_assistant

# Temporary build workspace on the Orin (separate from the deployment dir)
ORIN_BUILD_DIR ?= /tmp/voice_assistant_build

# Python interpreter on the Orin
ORIN_PYTHON ?= python3

PYTHON      ?= python3.8
VENV        ?= venv
PIP         := $(VENV)/bin/pip

.PHONY: all build run deploy install \
        service-start service-stop service-logs \
        setup-build-deps setup-dev setup-robot \
        clean clean-all help

all: build

# ─── Build (native on Orin over SSH) ─────────────────────────────────────────
#
# Nuitka does not support cross-compilation. We sync the source to the Orin,
# build there natively, then download the binary back to dist/.
#
# One-time setup: make setup-build-deps ROBOT_HOST=<orin-ip>

build:
	@echo ">>> Syncing source to $(ROBOT_USER)@$(ROBOT_HOST):$(ORIN_BUILD_DIR) …"
	ssh $(ROBOT_USER)@$(ROBOT_HOST) "mkdir -p $(ORIN_BUILD_DIR)"
	rsync -az --delete \
		--exclude=venv \
		--exclude=dist \
		--exclude='*.build' \
		--exclude='*.onefile-build' \
		--exclude=__pycache__ \
		--exclude=.git \
		. $(ROBOT_USER)@$(ROBOT_HOST):$(ORIN_BUILD_DIR)/
	@echo ">>> Building on $(ROBOT_HOST) (this may take several minutes) …"
	ssh -t $(ROBOT_USER)@$(ROBOT_HOST) " \
		set -e && \
		cd $(ORIN_BUILD_DIR) && \
		$(ORIN_PYTHON) -m venv venv && \
		venv/bin/pip install --quiet --upgrade pip wheel && \
		venv/bin/pip install --quiet 'setuptools<65' && \
		venv/bin/pip install --quiet -r requirements.txt && \
		venv/bin/pip install --quiet nuitka && \
		venv/bin/nuitka \
			--onefile \
			--follow-imports \
			--include-package=src \
			--include-package=dotenv \
			--include-package=openwakeword \
			--include-package=numpy \
			--include-package-data=openwakeword \
			--output-dir=dist \
			--output-filename=$(BINARY) \
			$(SRC) \
	"
	@echo ">>> Downloading binary …"
	mkdir -p $(DIST_DIR)
	scp $(ROBOT_USER)@$(ROBOT_HOST):$(ORIN_BUILD_DIR)/dist/$(BINARY) $(DIST_DIR)/$(BINARY)
	@echo ">>> Binary ready: $(DIST_DIR)/$(BINARY)"

# ─── Dev run (from source, local) ────────────────────────────────────────────

run: setup-dev
	@echo ">>> Running from source …"
	$(VENV)/bin/python $(SRC)

# ─── Deploy to Orin ───────────────────────────────────────────────────────────

deploy: build
	@echo ">>> Deploying to $(ROBOT_USER)@$(ROBOT_HOST):$(ROBOT_DIR) …"
	ssh $(ROBOT_USER)@$(ROBOT_HOST) "mkdir -p $(ROBOT_DIR)"
	scp $(DIST_DIR)/$(BINARY) $(ROBOT_USER)@$(ROBOT_HOST):$(ROBOT_DIR)/$(BINARY)
	scp $(SERVICE_SRC) $(ROBOT_USER)@$(ROBOT_HOST):$(ROBOT_DIR)/$(SERVICE_SRC)
	@if [ -f .env ]; then \
		echo ">>> Copying .env …"; \
		scp .env $(ROBOT_USER)@$(ROBOT_HOST):$(ROBOT_DIR)/.env; \
		ssh $(ROBOT_USER)@$(ROBOT_HOST) "chmod 600 $(ROBOT_DIR)/.env"; \
	else \
		echo ">>> WARNING: .env not found — copy .env.example to .env and fill in your values first."; \
		echo ">>>   cp .env.example .env && editor .env"; \
	fi
	@echo ">>> Deploy complete. Run 'make install' to install the service."

# ─── Install service on Orin ──────────────────────────────────────────────────

install:
	@echo ">>> Installing service on $(ROBOT_USER)@$(ROBOT_HOST) …"
	ssh -t $(ROBOT_USER)@$(ROBOT_HOST) " \
		chmod +x $(ROBOT_DIR)/$(BINARY) && \
		sudo cp $(ROBOT_DIR)/$(SERVICE_SRC) /etc/systemd/system/$(SERVICE_SRC) && \
		sudo systemctl daemon-reload && \
		sudo systemctl enable $(BINARY) && \
		sudo systemctl restart $(BINARY) && \
		echo 'Service installed and started.' \
	"

# ─── Service control (remote) ─────────────────────────────────────────────────

service-start:
	ssh -t $(ROBOT_USER)@$(ROBOT_HOST) "sudo systemctl start $(BINARY)"

service-stop:
	ssh -t $(ROBOT_USER)@$(ROBOT_HOST) "sudo systemctl stop $(BINARY)"

service-logs:
	ssh -t $(ROBOT_USER)@$(ROBOT_HOST) "journalctl -u $(BINARY) -f"

# ─── System deps — Orin runtime (one-time) ───────────────────────────────────
# Only libportaudio2 is needed — the binary is self-contained.

setup-robot:
	@echo ">>> Installing runtime deps on $(ROBOT_USER)@$(ROBOT_HOST) …"
	ssh -t $(ROBOT_USER)@$(ROBOT_HOST) \
		"sudo apt-get update && sudo apt-get install -y libportaudio2"

# ─── System deps — Orin build (one-time) ─────────────────────────────────────
# Installs everything needed to compile the binary with Nuitka on the Orin.

setup-build-deps:
	@echo ">>> Installing build dependencies on $(ROBOT_USER)@$(ROBOT_HOST) …"
	ssh -t $(ROBOT_USER)@$(ROBOT_HOST) " \
		sudo apt-get update && \
		sudo apt-get install -y \
			python3 python3-dev python3-venv \
			gcc patchelf \
			libportaudio2 portaudio19-dev \
	"
	@echo ">>> Build dependencies installed."

# ─── Python venv + pip deps (local, for make run) ─────────────────────────────

setup-dev: $(VENV)/bin/pip
	@echo ">>> Installing Python deps …"
	$(PIP) install --quiet --upgrade pip wheel
	$(PIP) install --quiet "setuptools<65"
	$(PIP) install --quiet -r requirements.txt

$(VENV)/bin/pip:
	@echo ">>> Creating venv with $(PYTHON) …"
	virtualenv -p $(PYTHON) $(VENV)
	$(VENV)/bin/pip install --quiet --upgrade pip

# ─── Clean ────────────────────────────────────────────────────────────────────

clean:
	@echo ">>> Cleaning build artifacts …"
	rm -rf $(DIST_DIR)
	@echo ">>> Done."

clean-all: clean
	@echo ">>> Removing venv …"
	rm -rf $(VENV)
	@echo ">>> Done."

# ─── Help ─────────────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "  make build              Build binary natively on Jetson Orin (via SSH)"
	@echo "  make run                Run from source locally (dev)"
	@echo "  make deploy             Build on Orin + deploy binary + .env + service"
	@echo "  make install            Install + enable systemd service on Orin"
	@echo "  make service-start      Start service on Orin"
	@echo "  make service-stop       Stop service on Orin"
	@echo "  make service-logs       Tail service logs on Orin"
	@echo "  make setup-build-deps   Install build deps on Orin (one-time)"
	@echo "  make setup-robot        Install libportaudio2 on Orin (one-time)"
	@echo "  make clean              Remove local build artifacts"
	@echo "  make clean-all          Remove build artifacts + local venv"
	@echo ""
	@echo "  Orin target (defaults):"
	@echo "    ROBOT_USER=$(ROBOT_USER)  ROBOT_HOST=$(ROBOT_HOST)  ROBOT_DIR=$(ROBOT_DIR)"
	@echo ""
	@echo "  Example:"
	@echo "    make deploy ROBOT_HOST=192.168.1.100 ROBOT_USER=ubuntu"
	@echo ""
