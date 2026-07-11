# Local CI targets — mirror what GitHub Actions runs so you can validate
# before pushing. Run 'make check' to execute the full local CI suite.

# Detect OS for platform-specific build targets
UNAME := $(shell uname -s 2>/dev/null || echo Windows)

# Run the full local CI suite (lint + tests)
.PHONY: check
check: lint test
	@echo ""
	@echo "$(TEXT_COLOR_GREEN)$(TEXT_BOLD)All checks passed.$(TEXT_RESET)"

# Run pytest with coverage (mirrors the 'Run tests with coverage' CI step)
.PHONY: test
test: check-python-venv
	@echo "Running tests …"
	@python -m pytest tests/ -v --cov=evealert --cov-report=term-missing

# Run pre-commit linting (mirrors the lint CI steps)
.PHONY: lint
lint: check-python-venv
	@echo "Running linters …"
	@pre-commit run --all-files

# Build Windows executable (requires pyinstaller, run on Windows)
.PHONY: build-windows
build-windows: check-python-venv
	@echo "Building Windows executable …"
	@pyinstaller \
		--onefile \
		--noconsole \
		--name EVE-Alert \
		--icon evealert/img/eve.ico \
		--add-data "evealert/img;img" \
		--add-data "evealert/sound;sound" \
		main.py
	@echo "$(TEXT_COLOR_GREEN)Build complete: dist/EVE-Alert.exe$(TEXT_RESET)"

# Build macOS app bundle + DMG (requires pyinstaller + portaudio, run on macOS)
.PHONY: build-macos
build-macos: check-python-venv
	@echo "Building macOS app bundle …"
	@pyinstaller \
		--windowed \
		--name "EVE Alert" \
		--add-data "evealert/img:img" \
		--add-data "evealert/sound:sound" \
		main.py
	@hdiutil create \
		-volname "EVE Alert" \
		-srcfolder "dist/EVE Alert.app" \
		-ov \
		-format UDZO \
		EVE-Alert-macOS.dmg
	@echo "$(TEXT_COLOR_GREEN)Build complete: EVE-Alert-macOS.dmg$(TEXT_RESET)"

# Install all dev dependencies
.PHONY: install-dev
install-dev: check-python-venv
	@echo "Installing dev dependencies …"
	@pip install -e ".[dev]"

# Install Windows build dependencies
.PHONY: install-build-windows
install-build-windows: check-python-venv
	@echo "Installing Windows build dependencies …"
	@pip install -e ".[build-windows]"

# Install macOS build dependencies
.PHONY: install-build-macos
install-build-macos: check-python-venv
	@echo "Installing macOS build dependencies …"
	@pip install -e ".[build-macos]"
	@brew install portaudio 2>/dev/null || true

# Help message for CI commands
.PHONY: help
help::
	@echo "  $(TEXT_UNDERLINE)Local CI:$(TEXT_UNDERLINE_END)"
	@echo "    check                       Run full local CI suite (lint + tests)"
	@echo "    test                        Run pytest with coverage"
	@echo "    lint                        Run pre-commit linters on all files"
	@echo "    build-windows               Build Windows .exe with PyInstaller"
	@echo "    build-macos                 Build macOS .app + .dmg with py2app"
	@echo "    install-dev                 Install dev/test dependencies"
	@echo "    install-build-windows       Install Windows build tools"
	@echo "    install-build-macos         Install macOS build tools"
	@echo ""
