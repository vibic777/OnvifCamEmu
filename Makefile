PYTHON ?= python
PIP ?= $(PYTHON) -m pip
PKG = src

.PHONY: help install install-dev venv test cov run doctor lint clean clean-all dist-win dist-linux dist-all

help:
	@echo "Targets: venv, install, install-dev, test, cov, run, doctor, lint, clean, clean-all, dist-win, dist-linux, dist-all"

install:
	$(PIP) install -e .

install-dev:
	$(PIP) install -e ".[dev]"

venv:
	$(PYTHON) -m venv .venv
	.venv/Scripts/python -m pip install -U pip || .venv/bin/python -m pip install -U pip
	.venv/Scripts/python -m pip install -e ".[dev]" || .venv/bin/python -m pip install -e ".[dev]"


test:
	$(PYTHON) -m pytest

cov:
	$(PYTHON) -m pytest --cov=onvif_emu --cov-report=term-missing

run:
	$(PYTHON) -m onvif_emu --no-gui --log-level INFO

doctor:
	$(PYTHON) -m onvif_emu doctor

lint:
	$(PYTHON) -m ruff check src tests

clean:
	$(PYTHON) scripts/clean.py

clean-all:
	$(PYTHON) scripts/clean.py --all

dist-win:
	$(PYTHON) packaging/build.py win

dist-linux:
	$(PYTHON) packaging/build.py linux

dist-all:
	$(PYTHON) packaging/build.py all
