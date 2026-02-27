# ─── Voice Assistant — Makefile ───────────────────────────────────────────────
#
# Targets:
#   make build              Compile voice_assistant binary with Nuitka (x86_64)
#   make build-orin         Build aarch64 binary natively on Jetson Orin via SSH
#   make run                Run from source (dev/testing)
#   make deploy             Build + SCP binary + assets to robot (x86_64)
#   make deploy-orin        build-orin + deploy aarch64 binary to Jetson Orin
#   make install            Install binary + service on robot (via ssh)
#   make service-start      Start systemd service on robot (via ssh)
#   make service-stop       Stop systemd service on robot (via ssh)
#   make service-logs       Tail service logs on robot (via ssh)
#   make setup-build-deps   Install system packages needed to build (one-time, local)
#   make setup-orin-build   Install build deps on Orin (one-time, via ssh)
#   make setup-robot        Install runtime deps on robot (one-time, via ssh)
#   make clean              Remove Nuitka build artifacts
#   make clean-all          Remove build artifacts + venv (full reset)
#
# Robot connection — override on the command line:
#   make deploy ROBOT_HOST=192.168.1.100 ROBOT_USER=ubuntu
#   make build-orin ROBOT_HOST=192.168.0.63 ROBOT_USER=ubuntu
# ──────────────────────────────────────────────────────────────────────────────

BINARY      := voice_assistant
SRC         := voice_assistant.py
DIST_DIR    := dist
SERVICE_SRC := voice_assistant.service

# Robot SSH target — override as needed
ROBOT_USER  ?= ubuntu
ROBOT_HOST  ?= 192.168.0.63
ROBOT_DIR   ?= /opt/voice_assistant

# Temporary build workspace on the Orin (separate from the deployment dir)
ORIN_BUILD_DIR ?= /tmp/voice_assistant_build

# Python interpreter on the Orin (python3 resolves to whatever is installed)
ORIN_PYTHON ?= python3

PYTHON      ?= python3.8
VENV        ?= venv
PIP         := $(VENV)/bin/pip

.PHONY: all build build-orin run deploy deploy-orin install \
        service-start service-stop service-logs \
        setup-build-deps setup-orin-build setup-dev setup-robot \
        clean clean-all help

all: build

# ─── Build (x86_64, local) ────────────────────────────────────────────────────

build: setup-dev
	@echo ">>> Compiling $(SRC) with Nuitka (python=$(PYTHON)) …"
	$(VENV)/bin/nuitka \
		--onefile \
		--follow-imports \
		--include-package=src \
		--include-package=dotenv \
		--include-package=openwakeword \
		--include-package=numpy \
		--include-package-data=openwakeword \
		--output-dir=$(DIST_DIR) \
		--output-filename=$(BINARY) \
		$(SRC)
	@echo ">>> Binary ready: $(DIST_DIR)/$(BINARY)"

# ─── Build (aarch64, Jetson Orin — native build over SSH) ─────────────────────
#
# Strategy: Nuitka does not support cross-compilation, so we sync the source
# to the Orin, build there natively, then download the binary back as
# dist/voice_assistant-aarch64.
#
# One-time setup on the Orin:
#   make setup-orin-build ROBOT_HOST=<orin-ip>

build-orin:
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
	@echo ">>> Downloading aarch64 binary …"
	mkdir -p $(DIST_DIR)
	scp $(ROBOT_USER)@$(ROBOT_HOST):$(ORIN_BUILD_DIR)/dist/$(BINARY) \
		$(DIST_DIR)/$(BINARY)-aarch64
	@echo ">>> Binary ready: $(DIST_DIR)/$(BINARY)-aarch64"

# ─── Dev run (from source) ────────────────────────────────────────────────────

run: setup-dev
	@echo ">>> Running from source …"
	$(VENV)/bin/python $(SRC)

# ─── Deploy to robot (x86_64) ─────────────────────────────────────────────────

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
		echo ">>> WARNING: .env not found locally — copy .env.example to .env and fill in your keys first."; \
		echo ">>>   cp .env.example .env && editor .env"; \
	fi
	@echo ">>> Deploy complete. Run 'make install' to install the service."

# ─── Deploy to Jetson Orin (aarch64) ─────────────────────────────────────────

deploy-orin: build-orin
	@echo ">>> Deploying aarch64 binary to $(ROBOT_USER)@$(ROBOT_HOST):$(ROBOT_DIR) …"
	ssh $(ROBOT_USER)@$(ROBOT_HOST) "mkdir -p $(ROBOT_DIR)"
	scp $(DIST_DIR)/$(BINARY)-aarch64 $(ROBOT_USER)@$(ROBOT_HOST):$(ROBOT_DIR)/$(BINARY)
	scp $(SERVICE_SRC) $(ROBOT_USER)@$(ROBOT_HOST):$(ROBOT_DIR)/$(SERVICE_SRC)
	@if [ -f .env ]; then \
		echo ">>> Copying .env …"; \
		scp .env $(ROBOT_USER)@$(ROBOT_HOST):$(ROBOT_DIR)/.env; \
		ssh $(ROBOT_USER)@$(ROBOT_HOST) "chmod 600 $(ROBOT_DIR)/.env"; \
	else \
		echo ">>> WARNING: .env not found locally — copy .env.example to .env and fill in your keys first."; \
		echo ">>>   cp .env.example .env && editor .env"; \
	fi
	@echo ">>> Deploy complete. Run 'make install' to install the service."

# ─── Install service on robot ─────────────────────────────────────────────────

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

# ─── System deps — robot runtime (one-time) ───────────────────────────────────
# Only libportaudio2 is needed — the binary is self-contained.

setup-robot:
	@echo ">>> Installing runtime deps on $(ROBOT_USER)@$(ROBOT_HOST) …"
	ssh -t $(ROBOT_USER)@$(ROBOT_HOST) \
		"sudo apt-get update && sudo apt-get install -y libportaudio2"

# ─── System deps — Jetson Orin build machine (one-time) ───────────────────────
# Installs everything needed to compile the binary with Nuitka on the Orin.

setup-orin-build:
	@echo ">>> Installing build dependencies on $(ROBOT_USER)@$(ROBOT_HOST) …"
	ssh -t $(ROBOT_USER)@$(ROBOT_HOST) " \
		sudo apt-get update && \
		sudo apt-get install -y \
			python3 python3-dev python3-venv \
			gcc patchelf \
			libportaudio2 portaudio19-dev \
	"
	@echo ">>> Orin build dependencies installed."

# ─── System deps — dev/build machine (one-time) ───────────────────────────────
# Installs everything needed to compile the binary locally with Nuitka.

setup-build-deps:
	@echo ">>> Installing build dependencies (requires sudo) …"
	sudo apt-get update
	sudo apt-get install -y \
		python3.8 \
		python3.8-dev \
		python3-virtualenv \
		gcc \
		patchelf \
		libportaudio2 \
		portaudio19-dev
	@echo ">>> Build dependencies installed."

# ─── Python venv + pip deps ───────────────────────────────────────────────────

setup-dev: $(VENV)/bin/pip
	@echo ">>> Installing Python deps …"
	$(PIP) install --quiet --upgrade pip wheel
	$(PIP) install --quiet "setuptools<65"
	$(PIP) install --quiet -r requirements.txt
	$(PIP) install nuitka

$(VENV)/bin/pip:
	@echo ">>> Creating venv with $(PYTHON) …"
	virtualenv -p $(PYTHON) $(VENV)
	$(VENV)/bin/pip install --quiet --upgrade pip

# ─── Clean ────────────────────────────────────────────────────────────────────

clean:
	@echo ">>> Cleaning build artifacts …"
	rm -rf $(DIST_DIR) $(BINARY).build $(BINARY).dist $(BINARY).onefile-build
	@echo ">>> Done."

clean-all: clean
	@echo ">>> Removing venv …"
	rm -rf $(VENV)
	@echo ">>> Done."

# ─── Help ─────────────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "  make build              Compile binary with Nuitka (x86_64, local)"
	@echo "  make build-orin         Build aarch64 binary natively on Jetson Orin"
	@echo "  make run                Run from source (dev)"
	@echo "  make deploy             Build + deploy x86_64 binary to robot"
	@echo "  make deploy-orin        Build + deploy aarch64 binary to Jetson Orin"
	@echo "  make install            Install + enable systemd service on robot"
	@echo "  make service-start      Start service on robot"
	@echo "  make service-stop       Stop service on robot"
	@echo "  make service-logs       Tail service logs on robot"
	@echo "  make setup-build-deps   Install system build deps locally (one-time)"
	@echo "  make setup-orin-build   Install build deps on Orin (one-time)"
	@echo "  make setup-robot        Install libportaudio2 on robot (one-time)"
	@echo "  make clean              Remove Nuitka build artifacts"
	@echo "  make clean-all          Remove build artifacts + venv"
	@echo ""
	@echo "  Robot target (defaults):"
	@echo "    ROBOT_USER=$(ROBOT_USER)  ROBOT_HOST=$(ROBOT_HOST)  ROBOT_DIR=$(ROBOT_DIR)"
	@echo ""
	@echo "  Jetson Orin build workspace (on Orin):"
	@echo "    ORIN_BUILD_DIR=$(ORIN_BUILD_DIR)  ORIN_PYTHON=$(ORIN_PYTHON)"
	@echo ""
	@echo "  Examples:"
	@echo "    make deploy ROBOT_HOST=192.168.1.100 ROBOT_USER=ubuntu"
	@echo "    make build-orin ROBOT_HOST=192.168.0.63 ROBOT_USER=ubuntu"
	@echo "    make deploy-orin ROBOT_HOST=192.168.0.63 ROBOT_USER=ubuntu"
	@echo ""
