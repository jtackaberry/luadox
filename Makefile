ASSETS = $(wildcard assets/*)
EXT = $(wildcard ext/* ext/*/*)
TAG := $(subst v,,$(or $(shell git describe --tags 2>/dev/null), $(shell echo v0.0.1)))
RELEASE := luadox-$(TAG)

luadox: build/pkg.zip
	echo '#!/usr/bin/env python3' > build/luadox
	cat build/pkg.zip >> build/luadox
	chmod 755 build/luadox

build/pkg: src/*
	mkdir -p build/pkg/luadox
	cp -a src/* build/pkg/luadox
	echo "import luadox; luadox.main()" > build/pkg/__main__.py
	touch build/pkg

build/pkg/ext: ext $(EXT)
	@echo "*** copying external modules"
	mkdir -p build/pkg/
	cp -a ext/* build/pkg/
	touch build/pkg/ext

build/pkg/assets: $(ASSETS)
	@echo "*** copying assets"
	mkdir -p build/pkg
	cp -a assets/ build/pkg
	touch build/pkg/assets

build/pkg.zip: build/pkg build/pkg/ext build/pkg/assets build/pkg/luadox/version.py
	@echo "*** creating bundle at build/luadox"
	find build -type d -name __pycache__ -prune -exec rm -rf "{}" \;
	cd build/pkg && zip -q -r ../pkg.zip .

ext: requirements.txt
	@echo "*** installing dependencies into ext/"
	pip3 install -t ./ext -r requirements.txt
	@# These aren't needed
	rm -rf ext/bin ext/*.dist-info

# Regenerate version file if anything likely to affect it has changed
build/pkg/luadox/version.py: .git/refs/tags .git/refs/heads
	@echo "*** creating version.py"
	@echo "# This is a generated file" > build/pkg/luadox/version.py
	@echo "__version__ = \"$(TAG)\"" >> build/pkg/luadox/version.py

docker: luadox
	docker build --pull .

.PHONY: release
release: build/luadox
	cd build && tar zcf $(RELEASE).tar.gz luadox
	cd build && zip $(RELEASE).zip luadox


.PHONY: clean
clean:
	rm -rf ext build
