# ─── Voice Assistant — Makefile ───────────────────────────────────────────────
#
# Targets:
#   make build            Compile voice_assistant binary with Nuitka
#   make run              Run from source (dev/testing)
#   make deploy           Build + SCP binary + assets to robot
#   make install          Install binary + service on robot (via ssh)
#   make service-start    Start systemd service on robot (via ssh)
#   make service-stop     Stop systemd service on robot (via ssh)
#   make service-logs     Tail service logs on robot (via ssh)
#   make setup-build-deps Install system packages needed to build (one-time, local)
#   make setup-robot      Install runtime deps on robot (one-time, via ssh)
#   make clean            Remove Nuitka build artifacts
#   make clean-all        Remove build artifacts + venv (full reset)
#
# Robot connection — override on the command line:
#   make deploy ROBOT_HOST=192.168.1.100 ROBOT_USER=ubuntu
# ──────────────────────────────────────────────────────────────────────────────

BINARY      := voice_assistant
SRC         := voice_assistant.py
DIST_DIR    := dist
SERVICE_SRC := voice_assistant.service

# Robot SSH target — override as needed
ROBOT_USER  ?= ubuntu
ROBOT_HOST  ?= 192.168.0.63
ROBOT_DIR   ?= /opt/voice_assistant

PYTHON      ?= python3.8
VENV        ?= venv
PIP         := $(VENV)/bin/pip

.PHONY: all build run deploy install \
        service-start service-stop service-logs \
        setup-build-deps setup-dev setup-robot \
        clean clean-all help

all: build

# ─── Build ────────────────────────────────────────────────────────────────────

build: setup-dev
	@echo ">>> Compiling $(SRC) with Nuitka (python=$(PYTHON)) …"
	$(VENV)/bin/nuitka \
		--onefile \
		--follow-imports \
		--include-package=src \
		--include-package-data=pvporcupine \
		--output-dir=$(DIST_DIR) \
		--output-filename=$(BINARY) \
		$(SRC)
	@echo ">>> Binary ready: $(DIST_DIR)/$(BINARY)"

# ─── Dev run (from source) ────────────────────────────────────────────────────

run: setup-dev
	@echo ">>> Running from source …"
	$(VENV)/bin/python $(SRC)

# ─── Deploy to robot ──────────────────────────────────────────────────────────

deploy: build
	@echo ">>> Deploying to $(ROBOT_USER)@$(ROBOT_HOST):$(ROBOT_DIR) …"
	ssh $(ROBOT_USER)@$(ROBOT_HOST) "mkdir -p $(ROBOT_DIR)"
	scp $(DIST_DIR)/$(BINARY) $(ROBOT_USER)@$(ROBOT_HOST):$(ROBOT_DIR)/$(BINARY)
	@if ls *.ppn 2>/dev/null | grep -q .; then \
		echo ">>> Copying .ppn wake word model(s) …"; \
		scp *.ppn $(ROBOT_USER)@$(ROBOT_HOST):$(ROBOT_DIR)/; \
	fi
	scp $(SERVICE_SRC) $(ROBOT_USER)@$(ROBOT_HOST):$(ROBOT_DIR)/$(SERVICE_SRC)
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

# ─── System deps — robot (one-time) ──────────────────────────────────────────
# Only libportaudio2 is needed — the binary is self-contained.

setup-robot:
	@echo ">>> Installing runtime deps on $(ROBOT_USER)@$(ROBOT_HOST) …"
	ssh -t $(ROBOT_USER)@$(ROBOT_HOST) \
		"sudo apt-get update && sudo apt-get install -y libportaudio2"

# ─── System deps — dev/build machine (one-time) ───────────────────────────────
# Installs everything needed to compile the binary with Nuitka.

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
	@echo "  make build              Compile binary with Nuitka"
	@echo "  make run                Run from source (dev)"
	@echo "  make deploy             Build + SCP to robot"
	@echo "  make install            Install + enable systemd service on robot"
	@echo "  make service-start      Start service on robot"
	@echo "  make service-stop       Stop service on robot"
	@echo "  make service-logs       Tail service logs on robot"
	@echo "  make setup-build-deps   Install system build deps locally (one-time)"
	@echo "  make setup-robot        Install libportaudio2 on robot (one-time)"
	@echo "  make clean              Remove Nuitka build artifacts"
	@echo "  make clean-all          Remove build artifacts + venv"
	@echo ""
	@echo "  Robot target (defaults):"
	@echo "    ROBOT_USER=$(ROBOT_USER)  ROBOT_HOST=$(ROBOT_HOST)  ROBOT_DIR=$(ROBOT_DIR)"
	@echo ""
	@echo "  Example:"
	@echo "    make deploy ROBOT_HOST=192.168.1.100 ROBOT_USER=ubuntu"
	@echo ""
